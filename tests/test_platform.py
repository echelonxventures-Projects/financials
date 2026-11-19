"""
Tests for the platform layer added on top of the engine: model registry, M&A configs,
guarded data fetching, the CLI's ma/chart/serve/init commands and the API's security,
M&A, charts and export endpoints.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from financial_model import __version__, config_loader
from financial_model.api.main import create_app
from financial_model.cli.main import app as cli_app
from financial_model.core.model import FinancialModel
from financial_model.data.loader import load_public_company_data, normalize_ticker
from financial_model.models import MODEL_CLASSES, build_model, normalize_model_type, register_model
from financial_model.models.lbo import LBOModel
from financial_model.models.ma import MergerModel
from financial_model.models.three_statement import ThreeStatementModel
from financial_model.utils.error_handler import DataFetchError
from financial_model.utils.rate_limiter import (
    CircuitBreakerOpen,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    reset_rate_limiter,
)
from financial_model.utils.validation import ValidationError

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
runner = CliRunner()


def _yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def limiter():
    lim = RateLimiter(RateLimitConfig(max_requests_per_minute=100, circuit_breaker_threshold=2))
    reset_rate_limiter(lim)
    yield lim
    reset_rate_limiter()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class TestRegistry:
    @pytest.mark.parametrize(
        "raw,key",
        [(None, "three-statement"), ("3s", "three-statement"), ("DCF", "dcf"), ("three_statement", "three-statement"),
         ("M&A", "ma"), ("merger", "ma"), (" lbo ", "lbo")],
    )
    def test_normalize(self, raw, key):
        assert normalize_model_type(raw) == key

    def test_unknown_type_lists_choices(self):
        with pytest.raises(ValueError, match="Choose from: three-statement, dcf, lbo, budget, ma"):
            normalize_model_type("nope")

    def test_build_model_dispatch(self):
        assert isinstance(build_model(_yaml("lbo_saas.yaml"), CONFIG_DIR), LBOModel)
        assert isinstance(build_model(_yaml("lbo_saas.yaml"), CONFIG_DIR, model_type="three-statement"), ThreeStatementModel)
        assert isinstance(build_model(_yaml("ma_saas.yaml"), CONFIG_DIR), MergerModel)

    def test_register_model(self):
        class Custom(ThreeStatementModel):
            model_type = "custom"

        register_model("custom", Custom)
        try:
            assert isinstance(build_model({"model_type": "custom"}), Custom)
        finally:
            MODEL_CLASSES.pop("custom")

    def test_register_rejects_non_models(self):
        with pytest.raises(TypeError):
            register_model("bad", dict)  # type: ignore[arg-type]

    def test_cli_and_api_share_the_registry(self):
        from financial_model.api import main as api_main
        from financial_model.cli import main as cli_main

        assert cli_main.MODEL_CLASSES is MODEL_CLASSES is api_main.MODEL_CLASSES


# --------------------------------------------------------------------------- #
# M&A configs
# --------------------------------------------------------------------------- #


class TestMergerConfig:
    def test_example_config(self):
        m = build_model(_yaml("ma_saas.yaml"), CONFIG_DIR)
        assert m.offer_price_per_share == 18.0 and m.synergies == 1_500_000
        tables = m.analysis_tables()
        assert set(tables) == {"Deal Summary", "Accretion Dilution", "Contribution"}
        summary = tables["Deal Summary"]
        assert summary["equity_value"] == pytest.approx(36_000_000)
        assert summary["cash_paid"] + summary["stock_value"] == pytest.approx(summary["equity_value"])
        assert list(tables["Accretion Dilution"].index) == ["FY2024", "FY2025", "FY2026", "FY2027", "FY2028"]

    def test_path_sides_resolve_relative_to_base_dir(self, tmp_path):
        (tmp_path / "acq.yaml").write_text(yaml.safe_dump(_yaml("ma_saas.yaml")["acquirer"]))
        cfg = {**_yaml("ma_saas.yaml"), "acquirer": "acq.yaml"}
        m = MergerModel.from_config(cfg, base_dir=tmp_path)
        assert m.acquirer.name == "Acquirer Co"

    def test_side_model_type_respected(self):
        cfg = {**_yaml("ma_saas.yaml"), "acquirer": {**_yaml("lbo_saas.yaml")}}
        assert isinstance(MergerModel.from_config(cfg, CONFIG_DIR).acquirer, LBOModel)

    def test_budget_side_rejected(self):
        cfg = {**_yaml("ma_saas.yaml"), "target": _yaml("budget_saas.yaml")}
        with pytest.raises(ValueError, match="three-statement, dcf or lbo"):
            MergerModel.from_config(cfg, CONFIG_DIR)

    def test_missing_side_and_unknown_terms(self):
        with pytest.raises(ValueError, match="'target'"):
            MergerModel.from_config({"acquirer": {}})
        with pytest.raises(ValueError, match="Unknown deal terms: bogus"):
            MergerModel.from_config({**_yaml("ma_saas.yaml"), "deal": {"bogus": 1}})

    def test_consideration_mix_completed_and_validated(self):
        base = _yaml("ma_saas.yaml")
        m = MergerModel.from_config({**base, "deal": {"stock_consideration_pct": 0.3}})
        assert m.cash_consideration_pct == pytest.approx(0.7)
        with pytest.raises(ValueError, match="must equal 1"):
            MergerModel.from_config({**base, "deal": {"cash_consideration_pct": 0.6, "stock_consideration_pct": 0.6}})

    def test_scenario_applies_to_sides_that_define_it(self):
        m = build_model(_yaml("ma_saas.yaml"), CONFIG_DIR)
        base = m.calculate_accretion_dilution()
        up = m.calculate_accretion_dilution("upside")  # only the target defines 'upside'
        assert up["by_period"][-1]["pf_eps"] > base["by_period"][-1]["pf_eps"]
        assert up["by_period"][-1]["acquirer_eps"] == pytest.approx(base["by_period"][-1]["acquirer_eps"])


# --------------------------------------------------------------------------- #
# Guarded data fetching
# --------------------------------------------------------------------------- #


def _company(empty: bool = False) -> MagicMock:
    company = MagicMock()
    frame = pd.DataFrame() if empty else pd.DataFrame({pd.Timestamp("2023-12-31"): {"Total Revenue": 1.0}})
    company.financials = frame
    company.balance_sheet = frame
    company.cashflow = frame
    company.info = {}
    return company


class TestGuardedFetch:
    def test_normalize_ticker(self):
        assert normalize_ticker(" brk-b ") == "BRK-B"
        with pytest.raises(ValidationError):
            normalize_ticker("rm -rf")

    @patch("financial_model.data.loader.yf.Ticker")
    def test_invalid_ticker_never_hits_network(self, mock_ticker, limiter):
        with pytest.raises(ValidationError):
            load_public_company_data("$$$")
        mock_ticker.assert_not_called()
        assert limiter.get_stats()["global_requests_last_hour"] == 0

    @patch("financial_model.data.loader.yf.Ticker")
    def test_success_is_counted(self, mock_ticker, limiter):
        mock_ticker.return_value = _company()
        data = load_public_company_data("aapl", 3)
        assert not data["income_statement"].empty
        assert limiter.get_stats()["global_requests_last_hour"] == 1

    @patch("financial_model.data.loader.yf.Ticker")
    def test_empty_statements_raise_and_count_failure(self, mock_ticker, limiter):
        mock_ticker.return_value = _company(empty=True)
        with pytest.raises(DataFetchError, match="no financial statements found for ZZZZ"):
            load_public_company_data("ZZZZ")
        assert limiter.get_stats()["failure_counts"]["yfinance"] == 1

    @patch("financial_model.data.loader.yf.Ticker")
    def test_provider_errors_open_the_circuit(self, mock_ticker, limiter):
        company = _company()
        type(company).financials = property(lambda self: (_ for _ in ()).throw(ConnectionError("boom")))
        mock_ticker.return_value = company
        for _ in range(2):
            with pytest.raises(DataFetchError, match="boom"):
                load_public_company_data("AAPL")
        with pytest.raises(CircuitBreakerOpen):
            load_public_company_data("AAPL")

    @patch("financial_model.data.loader.yf.Ticker")
    def test_rate_limit(self, mock_ticker):
        reset_rate_limiter(RateLimiter(RateLimitConfig(max_requests_per_minute=1)))
        try:
            mock_ticker.return_value = _company()
            load_public_company_data("AAPL")
            with pytest.raises(RateLimitExceeded):
                load_public_company_data("AAPL")
        finally:
            reset_rate_limiter()

    def test_years_must_be_positive(self):
        with pytest.raises(ValueError):
            load_public_company_data("AAPL", 0)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCLI:
    def test_version(self):
        r = runner.invoke(cli_app, ["--version"])
        assert r.exit_code == 0 and __version__ in r.output

    def test_ma_command_prints_and_exports(self, tmp_path):
        r = runner.invoke(cli_app, ["ma", str(CONFIG_DIR / "ma_saas.yaml"), "-o", str(tmp_path / "ma.xlsx")])
        assert r.exit_code == 0, r.output
        assert "Accretion / (dilution)" in r.output and "+1.8%" in r.output
        assert openpyxl.load_workbook(tmp_path / "ma.xlsx").sheetnames == [
            "Deal Summary",
            "Accretion Dilution",
            "Contribution",
        ]

    @pytest.mark.parametrize("out,check", [("ma.json", "file"), ("ma_csv", "dir")])
    def test_ma_other_formats(self, tmp_path, out, check):
        target = tmp_path / out
        r = runner.invoke(cli_app, ["run", str(CONFIG_DIR / "ma_saas.yaml"), "-o", str(target), "-s", "downside"])
        assert r.exit_code == 0, r.output
        if check == "file":
            assert "Accretion Dilution" in json.loads(target.read_text(encoding="utf-8"))
        else:
            assert (target / "accretion_dilution.csv").exists()

    def test_validate_ma(self):
        r = runner.invoke(cli_app, ["validate", str(CONFIG_DIR / "ma_saas.yaml")])
        assert r.exit_code == 0 and "ma" in r.output

    def test_init_ma_roundtrip(self, tmp_path):
        out = tmp_path / "deal.yaml"
        assert runner.invoke(cli_app, ["init", "--type", "ma", "-o", str(out)]).exit_code == 0
        r = runner.invoke(cli_app, ["ma", str(out)])
        assert r.exit_code == 0, r.output

    @pytest.mark.parametrize("cfg", ["three_statement_saas.yaml", "dcf_saas.yaml", "lbo_saas.yaml", "budget_saas.yaml", "ma_saas.yaml"])
    def test_chart_dashboard_for_every_example(self, tmp_path, cfg):
        out = tmp_path / "c.html"
        r = runner.invoke(cli_app, ["chart", str(CONFIG_DIR / cfg), "-o", str(out), "--cdn"])
        assert r.exit_code == 0, r.output
        assert out.read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_chart_directory_output(self, tmp_path):
        out = tmp_path / "charts"
        r = runner.invoke(cli_app, ["chart", str(CONFIG_DIR / "dcf_saas.yaml"), "-o", str(out), "--cdn", "--theme", "dark"])
        assert r.exit_code == 0, r.output
        assert (out / "dcf_bridge.html").exists()

    def test_run_dcf_excel_includes_valuation(self, tmp_path):
        out = tmp_path / "dcf.xlsx"
        assert runner.invoke(cli_app, ["run", str(CONFIG_DIR / "dcf_saas.yaml"), "-o", str(out)]).exit_code == 0
        assert "DCF Valuation" in openpyxl.load_workbook(out).sheetnames

    def test_dcf_command_requires_single_entity_config(self):
        r = runner.invoke(cli_app, ["dcf", str(CONFIG_DIR / "ma_saas.yaml")])
        assert r.exit_code != 0

    def test_missing_config_is_a_clean_error(self, tmp_path):
        r = runner.invoke(cli_app, ["run", str(tmp_path / "nope.yaml")])
        assert r.exit_code == 1 and "config file not found" in r.output

    def test_fetch_invalid_ticker(self, tmp_path):
        r = runner.invoke(cli_app, ["fetch", "BAD$", "-o", str(tmp_path / "x.yaml")])
        assert r.exit_code == 1 and "ticker" in r.output
        assert not (tmp_path / "x.yaml").exists()

    @patch("uvicorn.run")
    def test_serve_uses_settings(self, mock_run, tmp_path, monkeypatch):
        settings = tmp_path / "s.yaml"
        settings.write_text("api:\n  port: 9123\n  host: 0.0.0.0\n")
        monkeypatch.delenv("FIN_CONFIG_PATH", raising=False)
        try:
            r = runner.invoke(cli_app, ["serve", "--settings", str(settings)])
            assert r.exit_code == 0, r.output
            kwargs = mock_run.call_args.kwargs
            assert (kwargs["host"], kwargs["port"]) == ("0.0.0.0", 9123)
            r = runner.invoke(cli_app, ["serve", "--settings", str(settings), "--port", "1", "--host", "h"])
            assert (mock_run.call_args.kwargs["host"], mock_run.call_args.kwargs["port"]) == ("h", 1)
        finally:
            monkeypatch.delenv("FIN_CONFIG_PATH", raising=False)
            config_loader._reset_cache()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def _settings(**api) -> dict:
    cfg = config_loader.get_config(builtin=config_loader._BUILTIN_DEFAULTS)
    cfg["api"].update(api)
    return cfg


@pytest.fixture
def client():
    return TestClient(create_app(_settings()))


class TestAPISecurity:
    def test_api_key_required_when_configured(self):
        c = TestClient(create_app(_settings(api_key="s3cret")))
        assert c.get("/models").status_code == 401
        assert c.get("/models", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/models", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/health").status_code == 200  # liveness stays public

    def test_no_api_key_by_default(self, client):
        assert client.get("/models").status_code == 200

    def test_body_size_limit(self):
        c = TestClient(create_app(_settings(max_request_mb=0.001)))
        r = c.post("/calculate", content=b"x" * 5000, headers={"content-type": "application/json"})
        assert r.status_code == 413

    def test_cors_off_by_default_and_configurable(self, client):
        assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "https://x.io"}).headers
        c = TestClient(create_app(_settings(cors_origins=["https://x.io"])))
        r = c.get("/health", headers={"Origin": "https://x.io"})
        assert r.headers["access-control-allow-origin"] == "https://x.io"


class TestAPIEndpoints:
    def test_models_lists_ma(self, client):
        assert client.get("/models").json()["model_types"] == ["three-statement", "dcf", "lbo", "budget", "ma"]

    def test_health_version(self, client):
        assert client.get("/health").json() == {"status": "healthy", "version": __version__}

    def test_calculate_includes_analysis(self, client):
        r = client.post("/calculate", json={"config": _yaml("lbo_saas.yaml")})
        assert r.status_code == 200, r.text
        assert set(r.json()["analysis"]) == {"LBO Returns", "Sources", "Uses", "Debt Schedule"}

    def test_calculate_rejects_unknown_and_composite_types(self, client):
        r = client.post("/calculate", json={"config": {"model_type": "nope"}})
        assert r.status_code == 400 and "Unknown model type" in r.json()["detail"]
        r = client.post("/calculate", json={"config": {"model_type": "ma"}})
        assert r.status_code == 400 and "/ma endpoint" in r.json()["detail"]

    def test_ma_endpoint(self, client):
        r = client.post("/ma", json={"config": _yaml("ma_saas.yaml"), "scenario": "upside"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["Deal Summary"]["equity_value"] == pytest.approx(36_000_000)
        assert set(body["Accretion Dilution"]) == {"FY2024", "FY2025", "FY2026", "FY2027", "FY2028"}

    def test_ma_endpoint_rejects_file_paths(self, client):
        cfg = {**_yaml("ma_saas.yaml"), "acquirer": "../../etc/passwd"}
        r = client.post("/ma", json={"config": cfg})
        assert r.status_code == 400 and "file paths are not accepted" in r.json()["detail"]

    @pytest.mark.parametrize("cfg", ["dcf_saas.yaml", "ma_saas.yaml"])
    def test_charts_endpoint(self, client, cfg):
        r = client.post("/charts", json={"config": _yaml(cfg), "offline": False})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        assert "<!doctype html>" in r.text

    def test_dcf_includes_fcf_build(self, client):
        r = client.post("/dcf", json={"config": _yaml("dcf_saas.yaml")})
        assert r.status_code == 200
        assert "FY2024" in r.json()["free_cash_flow"]

    def test_export_excel_cleans_up_temp_file(self, client, tmp_path, monkeypatch):
        import tempfile

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        r = client.post("/export/excel", json={"config": {**_yaml("dcf_saas.yaml"), "name": "My/Model: v1"}})
        assert r.status_code == 200
        assert 'filename="My_Model__v1.xlsx"' in r.headers["content-disposition"]
        assert list(tmp_path.glob("*.xlsx")) == []

    @pytest.mark.parametrize(
        "exc,status",
        [(ValidationError("ticker", "bad"), 422), (RateLimitExceeded("slow down"), 429),
         (CircuitBreakerOpen("open"), 503), (DataFetchError("down"), 502), (RuntimeError("x"), 400)],
    )
    def test_fetch_error_mapping(self, client, exc, status):
        with patch("financial_model.api.main.fetch_statements", side_effect=exc):
            assert client.post("/fetch", json={"ticker": "AAPL"}).status_code == status

    def test_fetch_validates_ticker_without_mock(self, client):
        assert client.post("/fetch", json={"ticker": "../../x"}).status_code == 422


def test_financial_model_hook_default():
    assert FinancialModel().analysis_tables(None) == {}  # type: ignore[arg-type]
