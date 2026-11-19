"""
CLI tests.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import typer
from click.testing import CliRunner

from financial_model.cli.main import (
    _data_to_config,
    _export_csv,
    _export_json,
    _generate_config,
    _load_model,
    _print_summary,
    _synthetic_to_config,
    app,
)

# Get the Click command from Typer app for testing
click_app = typer.main.get_command(app)


class TestCLICommands:
    """Test CLI commands using Click's CliRunner."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_init_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "model.yaml"
            result = self.runner.invoke(
                click_app, ["init", "--output", str(output), "--type", "three-statement", "--years", "3"]
            )
            assert result.exit_code == 0
            assert output.exists()
            assert "Created" in result.output

    def test_init_with_industry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "model.yaml"
            result = self.runner.invoke(click_app, ["init", "--output", str(output), "--industry", "saas"])
            assert result.exit_code == 0
            assert "saas" in result.output.lower()

    def test_run_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # First create a config
            config_path = Path(tmpdir) / "model.yaml"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])

            # Run the model
            result = self.runner.invoke(click_app, ["run", str(config_path)])
            assert result.exit_code == 0
            assert "BS Balanced: True" in result.output or "BS Balanced: False" in result.output

    def test_run_with_output_excel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            output_path = Path(tmpdir) / "output.xlsx"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])
            result = self.runner.invoke(
                click_app, ["run", str(config_path), "--output", str(output_path), "--format", "excel"]
            )
            assert result.exit_code == 0
            assert output_path.exists()

    def test_run_with_output_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            output_path = Path(tmpdir) / "output.json"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])
            result = self.runner.invoke(
                click_app, ["run", str(config_path), "--output", str(output_path), "--format", "json"]
            )
            assert result.exit_code == 0
            assert output_path.exists()

    def test_run_with_output_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            output_path = Path(tmpdir) / "csv_output"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])
            result = self.runner.invoke(
                click_app, ["run", str(config_path), "--output", str(output_path), "--format", "csv"]
            )
            assert result.exit_code == 0
            assert output_path.exists()
            assert (output_path / "income_statement.csv").exists()

    def test_dcf_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "5"])
            result = self.runner.invoke(click_app, ["dcf", str(config_path)])
            assert result.exit_code == 0
            assert "WACC:" in result.output
            assert "EV (Gordon):" in result.output

    def test_lbo_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            # Use LBO model type to get proper LBO drivers
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--type", "lbo", "--years", "5"])
            result = self.runner.invoke(click_app, ["lbo", str(config_path)])
            assert result.exit_code == 0
            assert "Entry EV:" in result.output
            assert "MOIC:" in result.output
            assert "IRR:" in result.output

    @patch("financial_model.cli.main.fetch_statements")
    def test_fetch_command(self, mock_load):
        mock_load.return_value = {
            "income_statement": MagicMock(to_dict=lambda: {"Revenue": {}}),
            "balance_sheet": MagicMock(to_dict=lambda: {"Cash": {}}),
            "cash_flow": MagicMock(to_dict=lambda: {"Operating Cash Flow": {}}),
            "key_stats": {"market_cap": 1000000000},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "data.yaml"
            result = self.runner.invoke(click_app, ["fetch", "AAPL", "--output", str(output_path), "--years", "3"])
            assert result.exit_code == 0
            assert output_path.exists()

    def test_synthetic_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "synthetic.yaml"
            result = self.runner.invoke(
                click_app, ["synthetic", "--industry", "saas", "--output", str(output_path), "--years", "3"]
            )
            assert result.exit_code == 0
            assert output_path.exists()
            assert "saas" in result.output.lower()

    def test_synthetic_invalid_industry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "synthetic.yaml"
            result = self.runner.invoke(click_app, ["synthetic", "--industry", "invalid", "--output", str(output_path)])
            assert result.exit_code != 0
            assert "not yet implemented" in result.output

    def test_validate_command_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])
            result = self.runner.invoke(click_app, ["validate", str(config_path)])
            assert result.exit_code == 0
            assert "valid" in result.output.lower()

    def test_validate_command_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid.yaml"
            config_path.write_text("invalid: yaml: [")
            result = self.runner.invoke(click_app, ["validate", str(config_path)])
            assert result.exit_code != 0


class TestHelperFunctions:
    def setup_method(self):
        self.runner = CliRunner()

    def test_generate_config_three_statement(self):
        config = _generate_config("three-statement", "saas", 3, 2024)
        assert config["name"] == "Three Statement Model"
        assert config["periods"]["years"] == 3
        assert config["periods"]["start_year"] == 2024
        assert "revenue" in config["assumptions"]["drivers"]
        assert "revenue_growth" in config["assumptions"]["drivers"]
        assert "upside" in config["assumptions"]["scenarios"]
        assert "downside" in config["assumptions"]["scenarios"]

    def test_generate_config_other_industry(self):
        config = _generate_config("three-statement", "manufacturing", 5, 2025)
        assert config["periods"]["start_year"] == 2025
        assert config["periods"]["years"] == 5
        # Should have different default drivers for non-saas
        assert config["assumptions"]["drivers"]["revenue"]["value"] == 50_000_000

    def test_load_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            self.runner.invoke(click_app, ["init", "--output", str(config_path), "--years", "3"])
            model = _load_model(config_path)
            assert model is not None
            assert hasattr(model, "calculate")

    def test_data_to_config(self):
        data = {"income_statement": {}, "balance_sheet": {}, "cash_flow": {}}
        config = _data_to_config("AAPL", data, 5)
        assert config["name"] == "AAPL Three Statement Model"
        assert config["assumptions"]["metadata"]["ticker"] == "AAPL"

    def test_synthetic_to_config(self):
        data = {"periods": [], "income_statement": {}, "balance_sheet": {}, "cash_flow": {}}
        config = _synthetic_to_config(data, "saas", 3, 2024)
        assert config["periods"]["years"] == 3

    def test_print_summary(self, capsys):
        import pandas as pd

        from financial_model.core.balancer import BalanceResult
        from financial_model.core.model import ModelResult
        from financial_model.core.period import Period, PeriodIndex, PeriodType

        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

        result = ModelResult(
            income_statement=is_df,
            balance_sheet=bs_df,
            cash_flow=cf_df,
            assumptions=None,
            periods=periods,
            bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            metadata={"model_name": "Test"},
        )
        _print_summary(result)
        captured = capsys.readouterr()
        assert "Test" in captured.out
        assert "BS Balanced: True" in captured.out

    def test_export_csv(self):
        import pandas as pd

        from financial_model.core.balancer import BalanceResult
        from financial_model.core.model import ModelResult
        from financial_model.core.period import Period, PeriodIndex, PeriodType

        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

        result = ModelResult(
            income_statement=is_df,
            balance_sheet=bs_df,
            cash_flow=cf_df,
            assumptions=None,
            periods=periods,
            bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output"
            _export_csv(result, output_path)
            assert (output_path / "income_statement.csv").exists()
            assert (output_path / "balance_sheet.csv").exists()
            assert (output_path / "cash_flow.csv").exists()

    def test_export_json(self):
        import json

        import pandas as pd

        from financial_model.core.balancer import BalanceResult
        from financial_model.core.model import ModelResult
        from financial_model.core.period import Period, PeriodIndex, PeriodType

        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

        result = ModelResult(
            income_statement=is_df,
            balance_sheet=bs_df,
            cash_flow=cf_df,
            assumptions=None,
            periods=periods,
            bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.json"
            _export_json(result, output_path)
            assert output_path.exists()
            with open(output_path) as f:
                data = json.load(f)
            assert "income_statement" in data
            assert "balance_sheet" in data
            assert "cash_flow" in data
            assert "periods" in data
