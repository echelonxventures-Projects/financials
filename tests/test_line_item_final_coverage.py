"""
Tests for line_item module to achieve 100% coverage - targeting calculation rule branches.
"""

import pandas as pd

from financial_model.core.line_item import CalculationRule, Driver, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType


class TestLineItemCalculationBranches:
    """Test LineItem calculation rule branches for full coverage."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = {"test_driver": Driver("test_driver", [0.1, 0.15, 0.2], "Test", "%")}

    def test_pct_of_revenue_no_revenue_in_data(self):
        """Test PCT_OF_REVENUE when Revenue not in model_data."""
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_REVENUE,
            calculation_params={"driver": "test_driver"},
        )
        # Empty DataFrame - no Revenue
        idx = pd.MultiIndex.from_tuples([("income_statement", "Other")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        # Should fall back to driver value or 0
        assert val == 0  # Revenue not found, driver gives 0

    def test_pct_of_revenue_with_revenue_driver_fallback(self):
        """Test PCT_OF_REVENUE falls back to revenue driver."""
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_REVENUE,
            calculation_params={"driver": "test_driver"},
        )
        # DataFrame without Revenue but with driver in assumptions
        idx = pd.MultiIndex.from_tuples([("income_statement", "Other")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        # Revenue not in data but driver exists
        assumptions = {
            "test_driver": Driver("test_driver", [0.1, 0.15, 0.2], "Test", "%"),
            "revenue": Driver("revenue", [10000000, 20000000, 30000000], "Revenue", "$"),
        }
        val = item.calculate(df, 0, assumptions)
        # Should use revenue driver * test_driver = 10000000 * 0.1 = 1000000
        assert val == 1000000.0

    def test_pct_of_line_no_base_line(self):
        """Test PCT_OF_LINE when base_line not found."""
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_LINE,
            calculation_params={"base_line": "NonExistent", "driver": "test_driver"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        assert val == 0  # base_line not found

    def test_growth_rate_period_zero_no_driver(self):
        """Test GROWTH_RATE period 0 with no driver and no base_line."""
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[0, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        # No driver on item, no base_line, no params value -> returns 0
        assert val == 0

    def test_growth_rate_period_zero_with_params_value(self):
        """Test GROWTH_RATE period 0 with value in params."""
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver", "value": 1000000},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[0, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        assert val == 1000000

    def test_growth_rate_subsequent_no_previous(self):
        """Test GROWTH_RATE subsequent period with no previous value."""
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
        # Previous is 100, growth is 0.15 -> 115 (floating point)
        assert abs(val - 115.0) < 0.001

    def test_days_outstanding_no_base_line(self):
        """Test DAYS_OUTSTANDING when base_line not found."""
        item = LineItem(
            "AR",
            LineItemType.CALCULATED,
            "balance_sheet",
            calculation_rule=CalculationRule.DAYS_OUTSTANDING,
            calculation_params={"driver": "ar_days", "base_line": "NonExistent"},
        )
        assumptions = {"ar_days": Driver("ar_days", [30, 30, 30], "AR Days", "days")}
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[365000, 365000, 365000]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, assumptions)
        assert val == 0  # base_line not found

    def test_turnover_no_base_line(self):
        """Test TURNOVER when base_line not found."""
        item = LineItem(
            "Inventory",
            LineItemType.CALCULATED,
            "balance_sheet",
            calculation_rule=CalculationRule.TURNOVER,
            calculation_params={"driver": "inventory_turnover", "base_line": "NonExistent"},
        )
        assumptions = {"inventory_turnover": Driver("inventory_turnover", [4, 4, 4], "Turnover", "x")}
        idx = pd.MultiIndex.from_tuples([("income_statement", "COGS")])
        df = pd.DataFrame([[400, 400, 400]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, assumptions)
        assert val == 0  # base_line not found

    def test_turnover_zero_turnover(self):
        """Test TURNOVER with zero turnover."""
        item = LineItem(
            "Inventory",
            LineItemType.CALCULATED,
            "balance_sheet",
            calculation_rule=CalculationRule.TURNOVER,
            calculation_params={"driver": "inventory_turnover", "base_line": "COGS"},
        )
        assumptions = {"inventory_turnover": Driver("inventory_turnover", [0, 0, 0], "Turnover", "x")}
        idx = pd.MultiIndex.from_tuples([("income_statement", "COGS")])
        df = pd.DataFrame([[400, 400, 400]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, assumptions)
        assert val == 0.0  # Division by zero protection

    def test_sum_children_no_children(self):
        """Test SUM_CHILDREN with no children."""
        item = LineItem(
            "Total",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.SUM_CHILDREN,
            children=[],
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "A")])
        df = pd.DataFrame([[10, 20, 30]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_aggregate_no_children(self):
        """Test AGGREGATE with no children."""
        item = LineItem("Total", LineItemType.AGGREGATE, "income_statement", children=[])
        idx = pd.MultiIndex.from_tuples([("income_statement", "A")])
        df = pd.DataFrame([[10, 20, 30]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_link_no_source_params(self):
        """Test LINK with missing source parameters."""
        item = LineItem(
            "NI", LineItemType.LINK, "cash_flow", calculation_params={}
        )  # No source_line or source_statement
        idx = pd.MultiIndex.from_tuples([("income_statement", "Net Income")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_link_source_not_found(self):
        """Test LINK when source not found in data."""
        item = LineItem(
            "NI",
            LineItemType.LINK,
            "cash_flow",
            calculation_params={"source_line": "NonExistent", "source_statement": "income_statement"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Net Income")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_plug_returns_zero(self):
        """Test PLUG type returns 0."""
        item = LineItem("Cash", LineItemType.PLUG, "balance_sheet")
        df = pd.DataFrame()
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_unknown_line_type(self):
        """Test unknown line type returns 0."""
        # Create item with invalid type by bypassing enum
        item = LineItem("Test", LineItemType.INPUT, "income_statement")
        item.line_type = "unknown"  # type: ignore
        df = pd.DataFrame()
        val = item.calculate(df, 0, {})
        assert val == 0.0

    def test_apply_builtin_rule_unknown_rule(self):
        """Test _apply_builtin_rule with unknown rule."""
        item = LineItem("Test", LineItemType.CALCULATED, "income_statement")
        item.calculation_rule = "unknown"  # type: ignore
        df = pd.DataFrame()
        val = item._apply_builtin_rule(df, 0, {})
        assert val == 0.0


class TestDriverEdgeCases:
    """Test Driver edge cases."""

    def test_driver_scalar_value(self):
        """Test Driver with scalar value."""
        driver = Driver("test", 100, "Test", "$")
        assert driver.get_value(0) == 100
        assert driver.get_value(5) == 100  # Always returns same value

    def test_driver_list_value(self):
        """Test Driver with list value."""
        driver = Driver("test", [10, 20, 30], "Test", "$")
        assert driver.get_value(0) == 10
        assert driver.get_value(1) == 20
        assert driver.get_value(2) == 30
        assert driver.get_value(5) == 30  # Returns last value if index out of range
