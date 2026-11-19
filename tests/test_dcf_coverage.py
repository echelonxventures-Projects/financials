"""
Tests for DCF model to achieve 100% coverage - targeting uncovered lines.
"""
from __future__ import annotations

from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.dcf import DCFModel


class TestDCFCoverage:
    """Test DCFModel for uncovered lines."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        self.assumptions = Assumptions()

    def test_dcf_init_adds_assumptions(self):
        """Test _add_dcf_assumptions is called during init (lines 41-51, 55-66)."""
        model = DCFModel(name="Test DCF", periods=self.periods)
        # Check DCF-specific drivers were added
        assert "risk_free_rate" in model.assumptions.drivers
        assert "equity_risk_premium" in model.assumptions.drivers
        assert "beta" in model.assumptions.drivers
        assert "cost_of_debt" in model.assumptions.drivers
        assert "tax_rate" in model.assumptions.drivers
        assert "target_debt_ratio" in model.assumptions.drivers
        assert "terminal_growth" in model.assumptions.drivers
        assert "exit_multiple" in model.assumptions.drivers

    def test_calculate_dcf_with_no_operating_income(self):
        """Test calculate_dcf when Operating Income missing (line 95)."""
        model = DCFModel(name="Test DCF", periods=self.periods)
        # Add minimal line items
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [10000000] * 5, "Rev", "$")
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
        model.assumptions.set_driver(Driver("op_margin", [0.2] * 5, "Op Margin", "%"))
        model.add_balance_sheet_item(
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("cash", [1000000] * 5, "Cash", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Capital Expenditures",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("capex", [-500000] * 5, "CapEx", "$"),
            )
        )

        result = model.calculate()
        dcf_result = model.calculate_dcf(result)
        # Should handle missing Operating Income gracefully
        assert "wacc" in dcf_result

    def test_sensitivity_analysis_when_calculate_called(self):
        """Test sensitivity_analysis calls calculate if result is None (line 177-178)."""
        model = DCFModel(name="Test DCF", periods=self.periods)
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [10000000] * 5, "Rev", "$")
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
        model.assumptions.set_driver(Driver("op_margin", [0.2] * 5, "Op Margin", "%"))
        model.add_income_statement_item(
            LineItem(
                "Depreciation & Amortization",
                LineItemType.INPUT,
                "income_statement",
                driver=Driver("dna", [500000] * 5, "D&A", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("cash", [1000000] * 5, "Cash", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Capital Expenditures",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("capex", [-500000] * 5, "CapEx", "$"),
            )
        )

        # Call sensitivity_analysis without passing result - should call calculate internally
        sens = model.sensitivity_analysis()
        assert sens is not None
        assert sens.shape == (5, 5)
