"""
Tests for line_item - targeting remaining calculation rule branches.
"""

import pandas as pd

from financial_model.core.line_item import CalculationRule, Driver, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType


class TestLineItemRemainingBranches:
    """Test remaining uncovered branches in line_item calculation rules."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = {"test_driver": Driver("test_driver", [0.1, 0.15, 0.2], "Test", "%")}

    def test_link_fallback_to_simple_index(self):
        """Test LINK fallback when MultiIndex lookup fails (line 96)."""
        item = LineItem(
            "NI",
            LineItemType.LINK,
            "cash_flow",
            calculation_params={"source_line": "Net Income", "source_statement": "income_statement"},
        )
        # Simple index (not MultiIndex)
        df = pd.DataFrame([[100, 200, 300]], index=["Net Income"], columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 100

    def test_aggregate_simple_index(self):
        """Test AGGREGATE with simple index (lines 121-122)."""
        item = LineItem("Total", LineItemType.AGGREGATE, "income_statement", children=["A", "B"])
        df = pd.DataFrame([[10, 20, 30], [20, 30, 40]], index=["A", "B"], columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 30

    def test_pct_of_revenue_fallback_to_driver(self):
        """Test PCT_OF_REVENUE fallback to revenue driver (lines 175-176)."""
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_REVENUE,
            calculation_params={"driver": "test_driver"},
        )
        # No Revenue in data, but revenue driver exists
        df = pd.DataFrame(
            [[100, 200, 300]], index=[("income_statement", "Other")], columns=self.periods.to_dataframe_index()
        )
        assumptions = {
            "test_driver": Driver("test_driver", [0.1, 0.15, 0.2], "Test", "%"),
            "revenue": Driver("revenue", [10000000, 20000000, 30000000], "Revenue", "$"),
        }
        val = item.calculate(df, 0, assumptions)
        assert val == 1000000.0

    def test_growth_rate_base_line_lookup(self):
        """Test GROWTH_RATE base_line lookup across statements (lines 198-200)."""
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver", "base_line": "Starting ARR"},
        )
        # base_line "Starting ARR" in a different statement
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue"), ("balance_sheet", "Starting ARR")])
        df = pd.DataFrame([[0, 0, 0], [10000000, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        # Should use Starting ARR as base (10000000)
        assert val == 10000000

    def test_growth_rate_base_line_not_found_fallback(self):
        """Test GROWTH_RATE when base_line not found, falls back to driver (lines 202, 220-221)."""
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver", "base_line": "NonExistent"},
            driver=Driver("revenue", [10000000], "Revenue", "$"),
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[0, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        # Should fall back to item's driver value
        assert val == 10000000

    def test_growth_rate_subsequent_prev_val_lookup(self):
        """Test GROWTH_RATE subsequent period prev_val lookup (lines 228, 248-249)."""
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[100, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 1, self.assumptions)
        # Previous is 100, growth 0.15 -> 115
        assert abs(val - 115.0) < 0.001

    def test_days_outstanding_base_line_multiindex(self):
        """Test DAYS_OUTSTANDING base_line lookup in MultiIndex (lines 267-268)."""
        item = LineItem(
            "AR",
            LineItemType.CALCULATED,
            "balance_sheet",
            calculation_rule=CalculationRule.DAYS_OUTSTANDING,
            calculation_params={"driver": "ar_days", "base_line": "Revenue"},
        )
        assumptions = {"ar_days": Driver("ar_days", [30, 30, 30], "AR Days", "days")}
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[365000, 365000, 365000]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, assumptions)
        assert val == (30 / 365) * 365000

    def test_turnover_base_line_multiindex(self):
        """Test TURNOVER base_line lookup in MultiIndex (lines 287-292)."""
        item = LineItem(
            "Inventory",
            LineItemType.CALCULATED,
            "balance_sheet",
            calculation_rule=CalculationRule.TURNOVER,
            calculation_params={"driver": "inventory_turnover", "base_line": "COGS"},
        )
        assumptions = {"inventory_turnover": Driver("inventory_turnover", [4, 4, 4], "Turnover", "x")}
        idx = pd.MultiIndex.from_tuples([("income_statement", "COGS")])
        df = pd.DataFrame([[400, 400, 400]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, assumptions)
        assert val == 400 / 4
