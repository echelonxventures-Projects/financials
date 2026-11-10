"""
Core engine tests: period, line items, statements, balancing.
"""

import pytest
import pandas as pd
import numpy as np
from hypothesis import given, strategies as st, settings

from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule, Driver
from financial_model.core.statement import Statement, StatementType
from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.model import FinancialModel


class TestPeriod:
    def test_annual_period(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.label == "FY2024"
        assert p.start_date.year == 2024
        assert p.end_date.year == 2024
        assert p.end_date.month == 12

    def test_quarterly_period(self):
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        assert p.label == "Q1 FY2024"
        assert p.start_date.month == 1
        assert p.end_date.month == 3

    def test_monthly_period(self):
        p = Period(year=2024, month=6, period_type=PeriodType.MONTHLY)
        assert p.label == "06/2024"

    def test_period_next_prev(self):
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.next().year == 2025
        assert p.prev().year == 2023

        q = Period(year=2024, quarter=4, period_type=PeriodType.QUARTERLY)
        assert q.next().year == 2025
        assert q.next().quarter == 1

    def test_period_range(self):
        start = Period(year=2020, period_type=PeriodType.ANNUAL)
        end = Period(year=2024, period_type=PeriodType.ANNUAL)
        periods = Period.range(start, end)
        assert len(periods) == 5
        assert periods[0].year == 2020
        assert periods[-1].year == 2024


class TestPeriodIndex:
    def test_period_index_creation(self):
        periods = PeriodIndex([
            Period(year=2020, period_type=PeriodType.ANNUAL),
            Period(year=2021, period_type=PeriodType.ANNUAL),
            Period(year=2022, period_type=PeriodType.ANNUAL),
        ])
        assert len(periods) == 3

    def test_period_index_labels(self):
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        labels = periods.labels()
        assert labels == ["FY2020", "FY2021", "FY2022"]

    def test_period_index_dataframe_index(self):
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        df_idx = periods.to_dataframe_index()
        assert len(df_idx) == 3
        assert df_idx[0].year == 2020


class TestLineItem:
    def test_input_line_item(self):
        driver = Driver("revenue", 10_000_000, "Revenue", "$")
        item = LineItem("Revenue", LineItemType.INPUT, "income_statement", driver=driver)
        df = pd.DataFrame(index=["Revenue"], columns=[0, 1, 2], dtype=float)
        assumptions = {"revenue": driver}
        val = item.calculate(df, 0, assumptions)
        assert val == 10_000_000

    def test_pct_of_revenue(self):
        driver = Driver("cogs_pct", 0.3, "COGS %", "%")
        item = LineItem("COGS", LineItemType.CALCULATED, "income_statement",
                        calculation_rule=CalculationRule.PCT_OF_REVENUE,
                        calculation_params={"driver": "cogs_pct"},
                        driver=driver)
        df = pd.DataFrame(index=["Revenue", "COGS"], columns=[0, 1, 2], dtype=float)
        df.loc["Revenue"] = [100, 110, 121]
        assumptions = {"cogs_pct": driver}
        assert item.calculate(df, 0, assumptions) == 30
        assert item.calculate(df, 1, assumptions) == 33

    def test_growth_rate(self):
        driver = Driver("growth", 0.1, "Growth", "%")
        item = LineItem("Revenue", LineItemType.CALCULATED, "income_statement",
                        calculation_rule=CalculationRule.GROWTH_RATE,
                        calculation_params={"driver": "growth"},
                        driver=driver)
        # Create MultiIndex DataFrame like the model uses
        df = pd.DataFrame(index=["Revenue"], columns=[0, 1, 2], dtype=float)
        df.loc["Revenue", 0] = 100
        # Wrap in MultiIndex
        df.index = pd.MultiIndex.from_tuples([("income_statement", idx) for idx in df.index])
        assumptions = {"growth": driver}
        # Period 1: grows from period 0
        val1 = item.calculate(df, 1, assumptions)
        assert abs(val1 - 110) < 0.001
        # Update df to simulate sequential calculation
        df.loc[("income_statement", "Revenue"), 1] = val1
        # Period 2: grows from period 1
        val2 = item.calculate(df, 2, assumptions)
        assert abs(val2 - 121) < 0.001

    def test_days_outstanding(self):
        driver = Driver("ar_days", 45, "AR Days", "days")
        item = LineItem("AR", LineItemType.CALCULATED, "balance_sheet",
                        calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                        calculation_params={"driver": "ar_days", "base_line": "Revenue"},
                        driver=driver)
        df = pd.DataFrame(index=["Revenue", "AR"], columns=[0], dtype=float)
        df.loc["Revenue"] = 365_000_000
        assumptions = {"ar_days": driver}
        assert item.calculate(df, 0, assumptions) == 45_000_000


class TestStatement:
    def test_statement_validation(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        item = LineItem("Revenue", LineItemType.INPUT, "income_statement")
        stmt.add_line_item(item)
        errors = stmt.validate()
        assert len(errors) == 0

    def test_statement_missing_parent(self):
        stmt = Statement(StatementType.INCOME_STATEMENT)
        item = LineItem("Gross Profit", LineItemType.AGGREGATE, "income_statement", parent="Revenue")
        stmt.add_line_item(item)
        errors = stmt.validate()
        assert len(errors) > 0


class TestAssumptions:
    def test_driver_get_value(self):
        driver = Driver("growth", [0.1, 0.15, 0.2], "Growth", "%")
        assert driver.get_value(0) == 0.1
        assert driver.get_value(1) == 0.15
        assert driver.get_value(2) == 0.2
        assert driver.get_value(5) == 0.2  # Extends last value

    def test_scenario_override(self):
        assumptions = Assumptions(
            drivers={"growth": Driver("growth", 0.1, "Growth", "%")},
            scenarios={"upside": {"growth": 0.2}},
        )
        base = assumptions.get("growth")
        upside = assumptions.get("growth", scenario="upside")
        assert base.get_value(0) == 0.1
        assert upside.get_value(0) == 0.2

    def test_yaml_roundtrip(self, tmp_path):
        assumptions = Assumptions(
            drivers={"growth": Driver("growth", 0.1, "Growth", "%")},
            scenarios={"upside": {"growth": 0.2}},
            metadata={"source": "test"},
        )
        path = tmp_path / "assumptions.yaml"
        assumptions.to_yaml(str(path))
        loaded = Assumptions.from_yaml(str(path))
        assert loaded.get("growth").get_value(0) == 0.1
        assert loaded.get("growth", "upside").get_value(0) == 0.2


class TestBalancer:
    def test_balance_sheet_balancer_simple(self):
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        cols = periods.to_dataframe_index()

        bs = pd.DataFrame(index=[
            "Cash & Cash Equivalents",
            "Accounts Receivable",
            "Total Current Assets",
            "Property Plant & Equipment, Net",
            "Total Assets",
            "Accounts Payable",
            "Total Current Liabilities",
            "Long-Term Debt",
            "Total Liabilities",
            "Common Stock",
            "Retained Earnings",
            "Total Equity",
        ], columns=cols, dtype=float)

        # Set up simple BS
        bs.loc["Accounts Receivable"] = [100, 110, 121]
        bs.loc["Property Plant & Equipment, Net"] = [500, 500, 500]
        bs.loc["Accounts Payable"] = [50, 55, 60.5]
        bs.loc["Long-Term Debt"] = [200, 200, 200]
        bs.loc["Common Stock"] = [1000, 1000, 1000]

        # Initial: Assets = 100+500=600, L+E = 50+200+1000+RE=1250+RE
        # Need RE = -650 to balance, but RE is plug
        # Cash is also plug

        is_df = pd.DataFrame(index=["Net Income"], columns=cols, dtype=float)
        is_df.loc["Net Income"] = [50, 55, 60.5]

        cf_df = pd.DataFrame(index=["Net Change in Cash", "Beginning Cash Balance"], columns=cols, dtype=float)

        balancer = BalanceSheetBalancer()
        result = balancer.balance(bs, cf_df, is_df, periods, Assumptions())

        assert result.balanced
        # Assets should equal L+E
        for i in range(3):
            assets = bs.loc["Cash & Cash Equivalents"].iloc[i] + bs.loc["Accounts Receivable"].iloc[i] + bs.loc["Property Plant & Equipment, Net"].iloc[i]
            le = bs.loc["Accounts Payable"].iloc[i] + bs.loc["Long-Term Debt"].iloc[i] + bs.loc["Common Stock"].iloc[i] + bs.loc["Retained Earnings"].iloc[i]
            assert abs(assets - le) < 0.01


class TestFinancialModel:
    def test_three_statement_model_creation(self):
        from financial_model.models.three_statement import ThreeStatementModel
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        # Check default structure exists
        assert "Revenue" in model.income_statement.line_items
        assert "Cash & Cash Equivalents" in model.balance_sheet.line_items
        assert "Net Income" in model.cash_flow.line_items

    def test_model_calculation_runs(self):
        from financial_model.models.three_statement import ThreeStatementModel
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        result = model.calculate()

        assert result.income_statement.shape[1] == 3
        assert result.balance_sheet.shape[1] == 3
        assert result.cash_flow.shape[1] == 3
        assert result.bs_balance_result.balanced
        assert result.cf_balance_result.balanced


# Property-based tests
class TestProperties:
    @given(st.integers(min_value=2000, max_value=2050))
    @settings(max_examples=50)
    def test_period_year_bounds(self, year):
        p = Period(year=year, period_type=PeriodType.ANNUAL)
        assert p.year == year

    @given(st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_driver_positive_values(self, value):
        driver = Driver("test", value, "Test", "$")
        assert driver.get_value(0) == value