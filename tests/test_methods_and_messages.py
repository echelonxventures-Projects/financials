"""CLI wording, unknown-scenario errors and calculation method choices are configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from financial_model import config_loader
from financial_model.cli.main import app
from financial_model.cli.messages import messages, say
from financial_model.core.definitions import load_definition
from financial_model.core.model import ScenarioError
from financial_model.core.period import PeriodIndex
from financial_model.models.registry import MODEL_CLASSES, reload_definitions

CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture
def definitions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(tmp_path))
    config_loader._reset_cache()
    reload_definitions()
    yield tmp_path
    monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
    config_loader._reset_cache()
    reload_definitions()


def _write(directory, key, doc):
    (directory / f"{key}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    reload_definitions()


def _model(key):
    model = MODEL_CLASSES[key]()
    model.set_periods(PeriodIndex.generate(2024, 5))
    return model


class TestUnknownScenario:
    @pytest.mark.parametrize("command", ["run", "dcf", "chart"])
    def test_one_line_error(self, command, tmp_path):
        args = [command, str(CONFIG / "dcf_saas.yaml"), "--scenario", "nope"]
        if command == "chart":
            args += ["-o", str(tmp_path / "c.html")]
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1
        assert "Unknown scenario 'nope'" in result.output and "Traceback" not in result.output

    def test_scenario_error_is_a_value_error(self):
        from financial_model.models.registry import build_model

        model = build_model(yaml.safe_load((CONFIG / "dcf_saas.yaml").read_text()), base_dir=CONFIG)
        with pytest.raises(ScenarioError, match="Available"):
            model.calculate(scenario="nope")


class TestMessages:
    def test_every_message_formats(self):
        assert say("exported", output="x.xlsx") == "Exported to x.xlsx"
        assert say("api_key_once", key="k") == "api key (shown once): k"

    def test_settings_file_rewords_a_message(self, tmp_path, monkeypatch):
        settings = tmp_path / "s.yaml"
        settings.write_text(yaml.safe_dump({"cli": {"messages": {"init_done": "x", "created": "Écrit {output}"}}}))
        monkeypatch.setenv("FIN_CONFIG_PATH", str(settings))
        config_loader._reset_cache()
        try:
            out = tmp_path / "i.yaml"
            result = CliRunner().invoke(app, ["init", "-t", "dcf", "-o", str(out)])
            assert result.exit_code == 0 and result.output.strip() == f"Écrit {out}"
            assert "exported" in messages()  # untouched messages are kept
        finally:
            monkeypatch.delenv("FIN_CONFIG_PATH")
            config_loader._reset_cache()

    def test_unknown_message_key(self):
        with pytest.raises(KeyError, match="no CLI message"):
            say("no-such-message")


class TestMethodChoices:
    def test_mid_year_discounting_raises_value(self, definitions_dir):
        _write(
            definitions_dir,
            "dcf-mid",
            {"model_type": "dcf-mid", "extends": "dcf", "discounting": {"cash_flow_timing": 0.5}},
        )
        end, mid = _model("dcf"), _model("dcf-mid")
        end_pv = sum(end.calculate_dcf()["pv_fcf"])
        mid_dcf = mid.calculate_dcf()
        assert sum(mid_dcf["pv_fcf"]) > end_pv
        assert mid.discount_timing() == (0.5, 1.0)
        assert mid.sensitivity_analysis().shape == end.sensitivity_analysis().shape

    def test_sweep_order(self, definitions_dir):
        tranches = [t["name"] for t in load_definition("lbo")["tranches"] if t.get("amortizing")]
        reverse = list(reversed(tranches))
        _write(definitions_dir, "lbo-rev", {"model_type": "lbo-rev", "extends": "lbo", "sweep_order": reverse})
        model = _model("lbo-rev")
        assert model.sweep_order == tuple(reverse)
        result = model.calculate()
        assert result.bs_balance_result.balanced and result.cf_balance_result.balanced

    def test_unknown_tranche_in_sweep_order(self, definitions_dir):
        _write(definitions_dir, "lbo-bad", {"model_type": "lbo-bad", "extends": "lbo", "sweep_order": ["Nope"]})
        with pytest.raises(ValueError, match="sweep_order"):
            MODEL_CLASSES["lbo-bad"]()
