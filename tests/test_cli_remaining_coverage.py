"""
Tests for CLI remaining uncovered lines.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from financial_model.cli.main import _export_json, app
from financial_model.core.assumptions import Driver
from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.three_statement import ThreeStatementModel


class TestCLIRemainingCoverage:
    """Test CLI remaining uncovered lines."""

    def setup_method(self):
        # typer.testing.CliRunner takes the Typer app directly (no click needed)
        self.click_app = app
        self.runner = CliRunner()
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_cli_init_lbo_type(self):
        """Test init command with lbo type (line 92-93)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "lbo_model.yaml"
            result = self.runner.invoke(self.click_app, ["init", "--type", "lbo", "--output", str(output)])
            assert result.exit_code == 0
            assert output.exists()

    def test_cli_run_with_scenario(self):
        """Test run command with scenario (line 117)."""
        # Create a minimal model
        model = ThreeStatementModel(name="Test", periods=self.periods)
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [100, 200, 300], "Rev", "$")
            )
        )
        model.add_income_statement_item(
            LineItem(
                "Net Income",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "ni_margin"},
            )
        )
        model.assumptions.set_driver(Driver("ni_margin", [0.1, 0.1, 0.1], "NI Margin", "%"))

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            model.to_yaml(str(config_path))

            result = self.runner.invoke(self.click_app, ["run", str(config_path), "--scenario", "base"])
            assert result.exit_code == 0

    def test_cli_dcf_command(self):
        """Test dcf command (lines 164-167)."""
        model = ThreeStatementModel(name="Test", periods=self.periods)
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [100, 200, 300], "Rev", "$")
            )
        )
        model.add_income_statement_item(
            LineItem(
                "Operating Income",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "op_margin"},
            )
        )
        model.assumptions.set_driver(Driver("op_margin", [0.2, 0.2, 0.2], "Op Margin", "%"))
        model.add_income_statement_item(
            LineItem(
                "Depreciation & Amortization",
                LineItemType.INPUT,
                "income_statement",
                driver=Driver("dna", [10, 10, 10], "D&A", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("cash", [100, 100, 100], "Cash", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Capital Expenditures",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("capex", [-10, -10, -10], "CapEx", "$"),
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            model.to_yaml(str(config_path))

            result = self.runner.invoke(self.click_app, ["dcf", str(config_path)])
            assert result.exit_code == 0
            assert "WACC" in result.output

    def test_cli_lbo_command(self):
        """Test lbo command (line 292)."""
        from financial_model.models.lbo import LBOModel

        model = LBOModel(name="Test LBO", periods=self.periods)
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [100, 200, 300], "Rev", "$")
            )
        )
        model.add_income_statement_item(
            LineItem(
                "Operating Income",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "op_margin"},
            )
        )
        model.assumptions.set_driver(Driver("op_margin", [0.2, 0.2, 0.2], "Op Margin", "%"))
        model.add_income_statement_item(
            LineItem(
                "Depreciation & Amortization",
                LineItemType.INPUT,
                "income_statement",
                driver=Driver("dna", [10, 10, 10], "D&A", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("cash", [100, 100, 100], "Cash", "$"),
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            model.to_yaml(str(config_path))

            result = self.runner.invoke(self.click_app, ["lbo", str(config_path)])
            assert result.exit_code == 0
            assert "MOIC" in result.output

    def test_cli_main_block(self):
        """Test CLI main block (line 312)."""
        # This is the if __name__ == "__main__": app() line
        # We can't easily test it, but we can verify the module loads correctly
        from financial_model.cli import main

        assert hasattr(main, "app")

    def test_export_json_with_timestamp_keys(self):
        """Test _export_json with Timestamp keys (line 292 area)."""

        # Create a mock result with Timestamp keys
        class MockStatement:
            def to_dict(self):
                from pandas import Timestamp

                return {Timestamp("2024-12-31"): 100, Timestamp("2025-12-31"): 200}

            def to_string(self):
                return "Mock"

        class MockResult:
            def __init__(self):
                self.income_statement = MockStatement()
                self.balance_sheet = MockStatement()
                self.cash_flow = MockStatement()
                self.periods = [
                    Period(year=2024, period_type=PeriodType.ANNUAL),
                    Period(year=2025, period_type=PeriodType.ANNUAL),
                ]
                self.metadata = {"model_name": "Test"}
                self.bs_balance_result = type("obj", (object,), {"balanced": True, "max_diff": 0})()
                self.cf_balance_result = type("obj", (object,), {"balanced": True, "max_diff": 0})()

        result = MockResult()

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.json"
            _export_json(result, output)
            assert output.exists()

            # Verify JSON can be loaded
            import json

            with open(output) as f:
                data = json.load(f)
            assert "income_statement" in data
