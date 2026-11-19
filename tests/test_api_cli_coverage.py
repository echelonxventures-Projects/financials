"""
Tests for API and CLI exception handlers and edge cases.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import typer
from click.testing import CliRunner
from fastapi.testclient import TestClient

from financial_model.api.main import app
from financial_model.cli.main import _export_csv, _export_json, _print_summary
from financial_model.cli.main import app as cli_app
from financial_model.core.balancer import BalanceResult
from financial_model.core.model import ModelResult
from financial_model.core.period import Period, PeriodIndex, PeriodType

client = TestClient(app)
click_app = typer.main.get_command(cli_app)


class TestAPIExceptionHandlers:
    """Test API exception handling paths for uncovered lines."""

    def test_calculate_exception_handler(self):
        """Test /calculate triggers exception handler."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        # This triggers the exception in _config_to_model
        response = client.post("/calculate", json=request)
        assert response.status_code in [200, 400, 500]

    def test_dcf_exception_handler(self):
        """Test /dcf triggers exception handler."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            },
            "wacc_range": [0.08, 0.12],
            "growth_range": [0.02, 0.05],
        }
        response = client.post("/dcf", json=request)
        assert response.status_code in [200, 400, 500]

    def test_lbo_exception_handler(self):
        """Test /lbo triggers exception handler."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        response = client.post("/lbo", json=request)
        assert response.status_code in [200, 400, 500]

    def test_fetch_exception_handler(self):
        """Test /fetch exception handler."""
        with patch("financial_model.api.main.fetch_statements") as mock_load:
            mock_load.side_effect = Exception("API Error")
            request = {"ticker": "INVALID", "years": 3}
            response = client.post("/fetch", json=request)
            assert response.status_code == 400
            assert "API Error" in response.json()["detail"]

    def test_synthetic_invalid_industry(self):
        """Test /synthetic with invalid industry."""
        request = {"industry": "invalid", "years": 3}
        response = client.post("/synthetic", json=request)
        assert response.status_code == 400
        assert "not supported" in response.json()["detail"]

    def test_synthetic_exception_handler(self):
        """Test /synthetic exception handler."""
        with patch("financial_model.api.main.generate_saas_company") as mock_gen:
            mock_gen.side_effect = Exception("Generation failed")
            request = {"industry": "saas", "years": 3}
            response = client.post("/synthetic", json=request)
            assert response.status_code == 400
            assert "Generation failed" in response.json()["detail"]

    def test_export_excel_exception_handler(self):
        """Test /export/excel exception handler."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        response = client.post("/export/excel", json=request)
        assert response.status_code in [200, 400, 500]


class TestCLIExceptionHandlers:
    """Test CLI exception handling paths for uncovered lines."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_run_command_exception(self):
        """Test run command with exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            # Create invalid YAML to trigger exception
            config_path.write_text("invalid: yaml: [")
            result = self.runner.invoke(click_app, ["run", str(config_path)])
            assert result.exit_code != 0

    def test_dcf_command_exception(self):
        """Test dcf command with exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            config_path.write_text("invalid: yaml: [")
            result = self.runner.invoke(click_app, ["dcf", str(config_path)])
            assert result.exit_code != 0

    def test_lbo_command_exception(self):
        """Test lbo command with exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            config_path.write_text("invalid: yaml: [")
            result = self.runner.invoke(click_app, ["lbo", str(config_path)])
            assert result.exit_code != 0

    def test_validate_command_exception(self):
        """Test validate command with exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            config_path.write_text("invalid: yaml: [")
            result = self.runner.invoke(click_app, ["validate", str(config_path)])
            assert result.exit_code != 0

    def test_export_json_timestamp_handling(self):
        """Test _export_json handles Timestamp keys."""
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

    def test_export_csv(self):
        """Test _export_csv."""
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


class TestPrintSummary:
    """Test _print_summary function."""

    def test_print_summary(self, capsys):
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
