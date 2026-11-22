"""
Vendor / environment neutrality:

- the core runs with none of the optional packages (fastapi, uvicorn, plotly, yfinance)
- statements can come from any provider: local files (CSV / Excel / JSON in either
  orientation and common accounting formats), yfinance, or a registered plugin
- the API never lets a client read server files through a provider
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from financial_model import config_loader
from financial_model.api.main import create_app
from financial_model.data import (
    DataProvider,
    available_providers,
    derive_drivers_from_financials,
    fetch_statements,
    get_provider,
    register_provider,
    statements_from_mapping,
)
from financial_model.data import providers as providers_mod
from financial_model.data.loader import config_from_statements, next_projection_year
from financial_model.data.providers import normalize_statement_frame, resolve_provider_name
from financial_model.models import build_model
from financial_model.utils.error_handler import DataFetchError
from financial_model.utils.optional import MissingDependencyError, is_available, require
from financial_model.utils.plugins import discover
from financial_model.utils.validation import ValidationError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OPTIONAL = ("fastapi", "uvicorn", "starlette", "plotly", "yfinance")


@pytest.fixture(scope="module")
def model_and_result():
    m = build_model(yaml.safe_load((CONFIG_DIR / "three_statement_saas.yaml").read_text(encoding="utf-8")), CONFIG_DIR)
    return m, m.calculate()


def _derived(data):
    return derive_drivers_from_financials(
        data["income_statement"], data["balance_sheet"], data["cash_flow"], projection_years=5
    )


# --------------------------------------------------------------------------- #
# Minimal install
# --------------------------------------------------------------------------- #
_BLOCKER = textwrap.dedent(
    f"""
    import sys
    BLOCKED = set({OPTIONAL!r})
    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
    sys.meta_path.insert(0, Blocker())
    for mod in list(sys.modules):
        if mod.split(".")[0] in BLOCKED:
            del sys.modules[mod]
    """
)


def _run_minimal(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER + textwrap.dedent(code)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


class TestMinimalInstall:
    def test_core_api_without_optional_packages(self, tmp_path):
        proc = _run_minimal(
            f"""
            import yaml
            from pathlib import Path
            import financial_model.data, financial_model.exporters, financial_model.models
            from financial_model.models import build_model
            from financial_model.data import fetch_statements
            for name in ("three_statement_saas", "dcf_saas", "lbo_saas", "budget_saas", "ma_saas"):
                m = build_model(yaml.safe_load(open(f"config/{{name}}.yaml")), Path("config"))
                if name == "ma_saas":
                    m.analysis_tables()
                    continue
                r = m.calculate()
                assert r.bs_balance_result.balanced and r.cf_balance_result.balanced, name
                m.export_excel({str(tmp_path)!r} + f"/{{name}}.xlsx", r)
                m.export_csv({str(tmp_path)!r} + f"/{{name}}", r)
            data = fetch_statements({str(tmp_path)!r} + "/three_statement_saas", 5)
            assert data["provider"] == "file"
            for mod in {OPTIONAL!r}:
                assert mod not in __import__("sys").modules, mod
            print("ok")
            """
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("ok")

    @pytest.mark.parametrize(
        "args,extra",
        [(["chart", "config/dcf_saas.yaml"], "charts"), (["serve"], "api"), (["fetch", "SNOW"], "yfinance")],
    )
    def test_optional_features_fail_with_install_hint(self, args, extra):
        proc = _run_minimal(
            f"""
            import sys
            from financial_model.cli.main import app
            sys.argv = ["financial-model", *{args!r}]
            app()
            """
        )
        assert proc.returncode == 1
        assert f"pip install 'financial-model[{extra}]'" in proc.stdout + proc.stderr

    def test_cli_core_commands_work(self, tmp_path):
        proc = _run_minimal(
            f"""
            import sys
            from typer.testing import CliRunner
            from financial_model.cli.main import app
            r = CliRunner()
            for args in (["run", "config/lbo_saas.yaml", "-o", {str(tmp_path / "l.xlsx")!r}],
                         ["ma", "config/ma_saas.yaml"], ["validate", "config/dcf_saas.yaml"],
                         ["providers"], ["--version"]):
                res = r.invoke(app, args)
                assert res.exit_code == 0, (args, res.output)
            print("ok")
            """
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestOptionalHelper:
    def test_require_and_is_available(self):
        assert require("json", "x") is json
        assert is_available("json") and not is_available("definitely_not_a_module_xyz")

    def test_missing_dependency_names_extra(self):
        with pytest.raises(MissingDependencyError, match=r"pip install 'financial-model\[charts\]'") as info:
            require("plotly_does_not_exist", "Charts", extra="charts")
        assert isinstance(info.value, ImportError)

    def test_yfinance_provider_reports_missing_package(self, monkeypatch):
        from financial_model.data import loader

        monkeypatch.setattr(loader, "yf", None)
        with pytest.raises(MissingDependencyError, match="yfinance"):
            fetch_statements("AAPL", provider="yfinance")


# --------------------------------------------------------------------------- #
# File provider
# --------------------------------------------------------------------------- #
class TestFileProvider:
    def test_round_trips_every_export_format(self, model_and_result, tmp_path):
        model, result = model_and_result
        model.export_csv(tmp_path / "csv", result)
        model.export_json(tmp_path / "m.json", result)
        model.export_excel(tmp_path / "m.xlsx", result)
        derived = [_derived(fetch_statements(str(tmp_path / s), 5)) for s in ("csv", "m.json", "m.xlsx")]
        for d in derived:
            assert d["revenue"]["value"] == pytest.approx(result.income_statement.loc["Revenue"].iloc[-1])
            assert d["cogs_pct"]["value"] == pytest.approx(0.22)
            assert d["tax_rate"]["value"] == pytest.approx(0.21)
        assert derived[0] == derived[1] == derived[2]

    def test_hand_made_workbook(self, tmp_path):
        """Title rows, yfinance-style names, years as rows, accounting formats, P&L sheet name."""
        path = tmp_path / "acme.xlsx"
        pnl = pd.DataFrame(
            [["Acme Corp — P&L (USD)", None, None, None], [None, None, None, None],
             ["Line", "Total Revenue", "Cost Of Revenue", "Net Income"],
             ["FY2022", "1,000", "(400)", "100"], ["FY2023", "1,200", "(480)", "150"]]
        )
        bs = pd.DataFrame({"2022-12-31": {"Cash And Cash Equivalents": 50}, "2023-12-31": {"Cash And Cash Equivalents": 80}})
        with pd.ExcelWriter(path) as xw:
            pnl.to_excel(xw, sheet_name="P&L", header=False, index=False)
            bs.to_excel(xw, sheet_name="Balance Sheet")
        data = fetch_statements(str(path), 5)
        ist = data["income_statement"]
        assert list(ist.index) == ["Revenue", "COGS", "Net Income"]
        assert [c.year for c in ist.columns] == [2023, 2022]  # newest first, like yfinance
        assert ist.loc["COGS"].tolist() == [-480.0, -400.0]
        assert data["balance_sheet"].loc["Cash & Cash Equivalents"].tolist() == [80.0, 50.0]
        assert data["cash_flow"].empty

    def test_directory_with_mixed_files_and_key_stats(self, tmp_path):
        pd.DataFrame({"2023": {"Revenue": 10.0}, "2024": {"Revenue": 12.0}}).to_csv(tmp_path / "IS.csv")
        (tmp_path / "cash_flow.json").write_text(json.dumps({"FY2024": {"Capital Expenditures": -1}}))
        pd.DataFrame({"value": {"shares_outstanding": 1000}}).to_csv(tmp_path / "key_stats.csv")
        (tmp_path / "notes.txt").write_text("ignored")
        data = fetch_statements(str(tmp_path), 5)
        assert data["income_statement"].loc["Revenue"].tolist() == [12.0, 10.0]
        assert data["cash_flow"].loc["Capital Expenditures"].tolist() == [-1.0]
        assert data["key_stats"] == {"shares_outstanding": 1000}

    def test_years_keeps_newest_periods(self):
        df = pd.DataFrame({str(y): {"Revenue": float(y)} for y in range(2015, 2025)})
        out = normalize_statement_frame(df, "income_statement", years=3)
        assert out.loc["Revenue"].tolist() == [2024.0, 2023.0, 2022.0]

    def test_quarterly_labels_sort(self):
        df = pd.DataFrame({"Q2 FY2024": {"Revenue": 2.0}, "Q1 FY2024": {"Revenue": 1.0}, "Q4 FY2023": {"Revenue": 0.5}})
        out = normalize_statement_frame(df, "income_statement")
        assert out.loc["Revenue"].tolist() == [2.0, 1.0, 0.5]

    @pytest.mark.parametrize(
        "setup,match",
        [
            (lambda p: None, "not found"),
            (lambda p: p.mkdir(), "no income statement"),
            (lambda p: p.write_text("x"), "expected an .xlsx/.json"),
        ],
    )
    def test_bad_sources(self, tmp_path, setup, match):
        target = tmp_path / ("src.txt" if "xlsx" in match else "src")
        setup(target)
        with pytest.raises(ValidationError, match=match):
            fetch_statements(str(target), provider="file")

    def test_no_period_columns(self, tmp_path):
        (tmp_path / "x.json").write_text(json.dumps({"income_statement": {"abc": {"Revenue": 1}}}))
        with pytest.raises(ValidationError, match="no period columns"):
            fetch_statements(str(tmp_path / "x.json"))


# --------------------------------------------------------------------------- #
# Registry, resolution, plugins
# --------------------------------------------------------------------------- #
class EchoProvider(DataProvider):
    name = "echo"
    description = "test provider"
    remote = False

    def fetch(self, source, years):
        return {"income_statement": pd.DataFrame({"2024": {"Revenue": float(len(source))}}).pipe(
            normalize_statement_frame, "income_statement")}


class TestRegistry:
    @pytest.fixture(autouse=True)
    def cleanup(self):
        yield
        providers_mod._PROVIDERS.pop("echo", None)
        providers_mod._PROVIDERS.pop("echo2", None)

    def test_builtins(self):
        assert {"file", "yfinance"} <= set(available_providers())
        assert get_provider("FILE").remote is False and get_provider("yfinance").remote is True

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown data provider 'nope'"):
            fetch_statements("x", provider="nope")

    def test_register_and_use_custom_provider(self):
        register_provider(EchoProvider)
        data = fetch_statements("abcd", provider="echo")
        assert data["income_statement"].loc["Revenue"].tolist() == [4.0]
        assert data["provider"] == "echo" and data["key_stats"] == {} and data["balance_sheet"].empty

    def test_register_validation(self):
        with pytest.raises(TypeError):
            register_provider(object())  # type: ignore[arg-type]
        register_provider(EchoProvider(), name="echo2")
        assert "echo2" in available_providers()

    def test_empty_result_is_a_fetch_error(self):
        class Empty(EchoProvider):
            name = "echo"

            def fetch(self, source, years):
                return {"income_statement": pd.DataFrame()}

        register_provider(Empty)
        with pytest.raises(DataFetchError, match="no financial statements"):
            fetch_statements("x", provider="echo")

    def test_provider_exceptions_wrapped(self):
        class Boom(EchoProvider):
            def fetch(self, source, years):
                raise KeyError("bad layout")

        register_provider(Boom)
        with pytest.raises(DataFetchError, match="bad layout"):
            fetch_statements("x", provider="echo")

    def test_resolution_order(self, tmp_path, monkeypatch):
        assert resolve_provider_name(str(tmp_path), None) == "file"
        assert resolve_provider_name(str(tmp_path), None, for_api=True) == "yfinance"
        assert resolve_provider_name("AAPL", "echo") == "echo"
        monkeypatch.setenv("FIN_DATA_DEFAULT_PROVIDER", "echo")
        config_loader._reset_cache()
        try:
            assert resolve_provider_name("AAPL", None) == "echo"
        finally:
            monkeypatch.delenv("FIN_DATA_DEFAULT_PROVIDER")
            config_loader._reset_cache()

    def test_plugin_discovery_skips_broken_plugins(self, monkeypatch):
        class EP:
            def __init__(self, name, obj=None, exc=None):
                self.name, self._obj, self._exc = name, obj, exc

            def load(self):
                if self._exc:
                    raise self._exc
                return self._obj

        monkeypatch.setattr(
            "financial_model.utils.plugins._entry_points",
            lambda group: [EP("good", EchoProvider), EP("bad", exc=ImportError("missing dep"))],
        )
        assert discover("financial_model.data_providers") == {"good": EchoProvider}
        monkeypatch.setattr(providers_mod, "_plugins_loaded", False)
        assert "good" in available_providers()
        providers_mod._PROVIDERS.pop("good", None)

    def test_model_plugins(self, monkeypatch):
        from financial_model.models import registry
        from financial_model.models.three_statement import ThreeStatementModel

        class PluginModel(ThreeStatementModel):
            pass

        class EP:
            name = "plugin-model"

            def load(self):
                return PluginModel

        monkeypatch.setattr("financial_model.utils.plugins._entry_points", lambda group: [EP()])
        monkeypatch.setattr(registry, "_plugins_loaded", False)
        try:
            assert "plugin-model" in registry.available_model_types()
            assert isinstance(build_model({"model_type": "plugin-model"}), PluginModel)
        finally:
            registry.MODEL_CLASSES.pop("plugin-model", None)


class TestConfigFromStatements:
    def test_projection_starts_after_history(self, model_and_result):
        _, result = model_and_result
        data = statements_from_mapping(
            {"income_statement": result.labelled(result.income_statement).to_dict()}
        )
        assert next_projection_year(data["income_statement"]) == 2029
        cfg = config_from_statements(data, "X", years=3, metadata={"source": "test"})
        assert cfg["periods"] == {"start_year": 2029, "years": 3, "type": "annual"}
        assert cfg["assumptions"]["metadata"] == {"source": "test"}
        assert build_model(cfg).calculate().bs_balance_result.balanced

    def test_falls_back_without_history(self):
        assert next_projection_year(pd.DataFrame(), default=2030) == 2030


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    # Disable platform mode for agnostic API tests
    cfg = config_loader.get_config(builtin=config_loader._BUILTIN_DEFAULTS)
    cfg.setdefault("platform", {})["enabled"] = False
    return TestClient(create_app(cfg))


class TestAPI:
    def test_providers_endpoint_hides_server_file_access(self, client):
        names = [p["name"] for p in client.get("/providers").json()["providers"]]
        assert "yfinance" in names and "file" not in names

    def test_fetch_refuses_file_provider(self, client, tmp_path):
        r = client.post("/fetch", json={"ticker": str(tmp_path), "provider": "file"})
        assert r.status_code == 400 and "not available over the API" in r.json()["detail"]

    def test_fetch_never_autodetects_paths(self, client):
        # An existing server path must not switch the API to the file provider.
        r = client.post("/fetch", json={"ticker": str(CONFIG_DIR)})
        assert r.status_code == 422  # rejected as a malformed ticker instead

    def test_missing_optional_package_is_501(self, client, monkeypatch):
        from financial_model.data import loader

        monkeypatch.setattr(loader, "yf", None)
        r = client.post("/fetch", json={"ticker": "AAPL"})
        assert r.status_code == 501 and "financial-model[yfinance]" in r.json()["detail"]

    def test_import_then_calculate(self, client, model_and_result):
        _, result = model_and_result
        lab = lambda df: json.loads(json.dumps(result.labelled(df).to_dict()))  # noqa: E731
        body = {
            "name": "Imported Co",
            "income_statement": lab(result.income_statement),
            "balance_sheet": lab(result.balance_sheet),
            "cash_flow": lab(result.cash_flow),
            "years": 3,
        }
        r = client.post("/import", json=body)
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["periods"]["start_year"] == 2029
        assert cfg["assumptions"]["drivers"]["cogs_pct"]["value"] == pytest.approx(0.22)
        r = client.post("/calculate", json={"config": cfg})
        assert r.status_code == 200 and r.json()["bs_balanced"]

    def test_import_rejects_empty_income_statement(self, client):
        r = client.post("/import", json={"income_statement": {"FY2024": {"Revenue": "n/a"}}})
        assert r.status_code == 400
