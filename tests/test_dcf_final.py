"""
Tests for DCF model line 71.
"""
from __future__ import annotations

from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.dcf import DCFModel


class TestDCFLine71:
    """Test DCF calculate_dcf when result is None (line 71)."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        self.assumptions = Assumptions()

    def test_calculate_dcf_with_none_result(self):
        """Test calculate_dcf calls self.calculate() when result is None (line 71)."""
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

        # Call with result=None (default) - should call self.calculate() internally
        dcf_result = model.calculate_dcf(None)
        assert "wacc" in dcf_result
