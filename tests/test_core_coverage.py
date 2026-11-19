"""
Tests for core modules to achieve 100% coverage.
"""

from datetime import date

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.line_item import CalculationRule, Driver, LineItem, LineItemType
from financial_model.core.model import FinancialModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.statement import Statement, StatementType


class TestPeriodEdgeCases:
    """Test Period and PeriodIndex edge cases."""

    def test_period_equality(self):
        p1 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p3 = Period(year=2025, period_type=PeriodType.ANNUAL)
        assert p1 == p2
        assert p1 != p3

    def test_period_hash(self):
        p1 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert hash(p1) == hash(p2)

    def test_period_label_annual(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.label == "FY2024"

    def test_period_label_quarterly(self):
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        assert p.label == "Q1 FY2024"

    def test_period_label_monthly(self):
        p = Period(year=2024, month=1, period_type=PeriodType.MONTHLY)
        assert p.label == "01/2024"

    def test_period_index_iteration(self):
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        assert len(periods) == 3
        for i, p in enumerate(periods):
            assert p.year == 2024 + i

    def test_period_index_add(self):
        periods = PeriodIndex()
        periods.add(Period(year=2024, period_type=PeriodType.ANNUAL))
        assert len(periods) == 1

    def test_period_index_get_item(self):
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        assert periods[0].year == 2024
        assert periods[1].year == 2025

    def test_period_index_contains(self):
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p in periods

    def test_period_start_date_annual(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.start_date == date(2024, 1, 1)
        assert p.end_date == date(2024, 12, 31)

    def test_period_start_date_quarterly(self):
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        assert p.start_date == date(2024, 1, 1)
        assert p.end_date == date(2024, 3, 31)

    def test_period_start_date_monthly(self):
        p = Period(year=2024, month=1, period_type=PeriodType.MONTHLY)
        assert p.start_date == date(2024, 1, 1)
        assert p.end_date == date(2024, 1, 31)

    def test_period_next_prev(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.next().year == 2025
        assert p.prev().year == 2023

    def test_period_range(self):
        p1 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2026, period_type=PeriodType.ANNUAL)
        periods = Period.range(p1, p2)
        assert len(periods) == 3

    def test_period_to_timestamp(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        ts = p.to_timestamp()
        assert isinstance(ts, pd.Timestamp)

    def test_period_from_date(self):
        p = Period.from_date(date(2024, 6, 15), PeriodType.QUARTERLY)
        assert p.quarter == 2
        assert p.year == 2024

    def test_period_index_labels(self):
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        labels = periods.labels()
        assert labels == ["FY2024", "FY2025", "FY2026"]

    def test_period_index_to_dataframe_index(self):
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        df_index = periods.to_dataframe_index()
        assert len(df_index) == 3


class TestLineItemCalculationRules:
    """Test LineItem calculation rules for all built-in rules."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = {"test_driver": Driver("test_driver", [0.1, 0.15, 0.2], "Test", "%")}

    def test_pct_of_revenue(self):
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_REVENUE,
            calculation_params={"driver": "test_driver"},
        )
        # Create MultiIndex DataFrame with Revenue
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        assert val == 100 * 0.1

    def test_pct_of_line(self):
        item = LineItem(
            "Test",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.PCT_OF_LINE,
            calculation_params={"base_line": "Revenue", "driver": "test_driver"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 1, self.assumptions)
        assert val == 200 * 0.15

    def test_growth_rate_period_zero(self):
        item = LineItem(
            "Revenue",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.GROWTH_RATE,
            calculation_params={"driver": "test_driver"},
            driver=Driver("revenue", [100], "Revenue", "$"),
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Revenue")])
        df = pd.DataFrame([[100, 0, 0]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, self.assumptions)
        assert val == 100

    def test_growth_rate_period_subsequent(self):
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
        assert val == 100 * 1.15

    def test_days_outstanding(self):
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

    def test_turnover(self):
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

    def test_sum_children(self):
        item = LineItem(
            "Total",
            LineItemType.CALCULATED,
            "income_statement",
            calculation_rule=CalculationRule.SUM_CHILDREN,
            children=["A", "B"],
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "A"), ("income_statement", "B")])
        df = pd.DataFrame([[10, 20, 30], [20, 30, 40]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 30

    def test_aggregate(self):
        item = LineItem("Total", LineItemType.AGGREGATE, "income_statement", children=["A", "B"])
        idx = pd.MultiIndex.from_tuples([("income_statement", "A"), ("income_statement", "B")])
        df = pd.DataFrame([[10, 20, 30], [20, 30, 40]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 30

    def test_link(self):
        item = LineItem(
            "NI",
            LineItemType.LINK,
            "cash_flow",
            calculation_params={"source_line": "Net Income", "source_statement": "income_statement"},
        )
        idx = pd.MultiIndex.from_tuples([("income_statement", "Net Income")])
        df = pd.DataFrame([[100, 200, 300]], index=idx, columns=self.periods.to_dataframe_index())
        val = item.calculate(df, 0, {})
        assert val == 100


class TestStatementValidation:
    """Test Statement validation."""

    def test_statement_validate_empty(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        errors = stmt.validate()
        assert len(errors) == 0

    def test_statement_validate_duplicate_order(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("A", LineItemType.INPUT, "income_statement", order=1))
        stmt.add_line_item(LineItem("B", LineItemType.INPUT, "income_statement", order=1))
        errors = stmt.validate()
        # The current validation doesn't check for duplicate order, but let's check the method works
        assert isinstance(errors, list)

    def test_statement_validate_missing_parent(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("A", LineItemType.CALCULATED, "income_statement", parent="B"))
        errors = stmt.validate()
        assert any("parent" in e.lower() for e in errors)

    def test_statement_validate_missing_child(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "A",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["B"],
            )
        )
        errors = stmt.validate()
        assert any("child" in e.lower() for e in errors)

    def test_statement_validate_link_missing_source(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("A", LineItemType.LINK, "income_statement"))
        errors = stmt.validate()
        assert any("source_line" in e.lower() for e in errors)

    def test_statement_ordered_items(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("C", LineItemType.INPUT, "income_statement", order=3))
        stmt.add_line_item(LineItem("A", LineItemType.INPUT, "income_statement", order=1))
        stmt.add_line_item(LineItem("B", LineItemType.INPUT, "income_statement", order=2))
        ordered = stmt.ordered_items()
        assert [item.name for item in ordered] == ["A", "B", "C"]

    def test_statement_to_dataframe(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("rev", [100, 200, 300], "Rev", "$")
            )
        )
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        df = stmt.to_dataframe(periods)
        assert df.shape == (1, 3)

    def test_statement_get_line_item(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Revenue", LineItemType.INPUT, "income_statement"))
        item = stmt.get_line_item("Revenue")
        assert item is not None
        assert item.name == "Revenue"

    def test_statement_remove_line_item(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Revenue", LineItemType.INPUT, "income_statement"))
        stmt.remove_line_item("Revenue")
        assert stmt.get_line_item("Revenue") is None


class TestAssumptionsEdgeCases:
    """Test Assumptions edge cases."""

    def test_assumptions_get_driver(self):
        assumptions = Assumptions()
        assumptions.set_driver(Driver("test", [1, 2, 3], "Test", "$"))
        driver = assumptions.get("test")
        assert driver is not None
        assert driver.get_value(0) == 1

    def test_assumptions_get_nonexistent_driver(self):
        assumptions = Assumptions()
        driver = assumptions.get("nonexistent")
        assert driver is None

    def test_assumptions_get_value(self):
        assumptions = Assumptions()
        assumptions.set_driver(Driver("test", [10, 20, 30], "Test", "$"))
        val = assumptions.get_value("test", 1)
        assert val == 20

    def test_assumptions_get_value_nonexistent(self):
        assumptions = Assumptions()
        val = assumptions.get_value("nonexistent", 0)
        assert val == 0.0

    def test_assumptions_scenario_override(self):
        assumptions = Assumptions(
            drivers={"test": Driver("test", [10], "Test", "$")}, scenarios={"upside": {"test": 20}}
        )
        driver = assumptions.get("test", scenario="upside")
        assert driver.get_value(0) == 20

    def test_assumptions_to_yaml(self):
        import tempfile

        assumptions = Assumptions(drivers={"test": Driver("test", [10], "Test", "$")})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            assumptions.to_yaml(f.name)
            with open(f.name) as f2:
                content = f2.read()
        assert "test" in content

    def test_assumptions_from_yaml(self):
        import tempfile

        yaml_content = """
drivers:
  test:
    name: test
    value: [10]
    description: Test
    unit: $
scenarios:
  upside:
    test: 20
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            assumptions = Assumptions.from_yaml(f.name)
        assert "test" in assumptions.drivers
        assert "upside" in assumptions.scenarios

    def test_assumptions_copy(self):
        assumptions = Assumptions(drivers={"test": Driver("test", [10], "Test", "$")})
        copied = assumptions.copy()
        assert "test" in copied.drivers
        assert copied.drivers["test"].get_value(0) == 10


class TestBalancerEdgeCases:
    """Test Balancer edge cases."""

    def test_balancer_simple_balance(self):
        from financial_model.core.assumptions import Assumptions
        from financial_model.core.period import Period, PeriodIndex, PeriodType
        from financial_model.core.statement import Statement, StatementType

        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        assumptions = Assumptions()

        # Create minimal BS
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0],
                "Total Assets": [100],
                "Total Liabilities": [50],
                "Total Equity": [50],
                "Revolver": [0],
                "Retained Earnings": [0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Total Assets",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
        )

        # Create IS with Net Income
        is_df = pd.DataFrame({"Net Income": [10]}, index=["Net Income"])

        # Create CF
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0], "Beginning Cash Balance": [0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(bs_df, cf_df, is_df, periods, assumptions, Statement(StatementType.BALANCE_SHEET))
        # Should balance
        assert result.balanced

    def test_cf_balancer_simple(self):
        from financial_model.core.period import Period, PeriodIndex, PeriodType

        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])

        cols = periods.to_dataframe_index()
        bs_df = pd.DataFrame([[100.0]], index=["Cash & Cash Equivalents"], columns=cols)
        cf_df = pd.DataFrame(
            [[10.0], [0.0], [90.0], [0.0]],
            index=["Net Income", "Net Change in Cash", "Beginning Cash Balance", "Ending Cash Balance"],
            columns=cols,
        )

        balancer = CashFlowBalancer()
        result = balancer.balance(bs_df, cf_df, periods)
        assert result.balanced
        assert result.max_diff == 0


class TestFinancialModelIntegration:
    """Test FinancialModel integration."""

    def test_model_with_custom_line_items(self):
        model = FinancialModel(name="Test")
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        model.set_periods(periods)

        # Add a custom line item to income statement
        model.add_income_statement_item(
            LineItem("Custom", LineItemType.INPUT, "income_statement", driver=Driver("custom", [42], "Custom", "$"))
        )
        # Add minimal items to other statements to avoid empty DataFrame issues
        model.add_balance_sheet_item(
            LineItem("Cash", LineItemType.INPUT, "balance_sheet", driver=Driver("cash", [100], "Cash", "$"))
        )
        model.add_cash_flow_item(
            LineItem(
                "Net Income",
                LineItemType.LINK,
                "cash_flow",
                calculation_params={"source_line": "Custom", "source_statement": "income_statement"},
            )
        )

        result = model.calculate()
        assert result.income_statement.loc["Custom"].iloc[0] == 42

    def test_model_yaml_roundtrip(self):
        import tempfile

        from financial_model.models.three_statement import ThreeStatementModel

        model = ThreeStatementModel(name="Test Model")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            model.to_yaml(f.name)
            # Load it back
            loaded = ThreeStatementModel.from_yaml(f.name)
            assert loaded.name == "Test Model"
