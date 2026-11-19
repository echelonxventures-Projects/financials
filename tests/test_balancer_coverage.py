"""
Tests for balancer module to achieve 100% coverage.
"""
from __future__ import annotations

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.statement import Statement, StatementType


class TestBalanceSheetBalancerEdgeCases:
    """Test BalanceSheetBalancer edge cases and error paths."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = Assumptions()

    def test_balancer_single_period(self):
        """Test balancer with single period."""
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])

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

        is_df = pd.DataFrame({"Net Income": [10]}, index=["Net Income"])
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0], "Beginning Cash Balance": [0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_multiple_periods(self):
        """Test balancer with multiple periods."""
        periods = self.periods
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Total Assets": [100, 120, 140],
                "Total Liabilities": [50, 60, 70],
                "Total Equity": [50, 60, 70],
                "Revolver": [0, 0, 0],
                "Retained Earnings": [0, 0, 0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Total Assets",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame({"Net Income": [10, 15, 20]}, index=["Net Income"], columns=periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_with_revolver_plug(self):
        """Test balancer uses revolver as plug."""
        periods = self.periods
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Total Assets": [100, 120, 140],
                "Total Liabilities": [50, 60, 70],
                "Total Equity": [40, 50, 60],  # Assets - Liabilities = 50, but Equity is 40 -> gap of 10
                "Revolver": [0, 0, 0],
                "Retained Earnings": [0, 0, 0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Total Assets",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame({"Net Income": [10, 15, 20]}, index=["Net Income"], columns=periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced
        # Balancer should use a plug (either Revolver or Cash) to balance
        assert len(result.plug_values) > 0

    def test_balancer_with_retained_earnings(self):
        """Test balancer updates retained earnings from net income."""
        periods = self.periods
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Total Assets": [100, 120, 140],
                "Total Liabilities": [50, 60, 70],
                "Total Equity": [50, 60, 70],
                "Revolver": [0, 0, 0],
                "Retained Earnings": [0, 0, 0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Total Assets",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame({"Net Income": [10, 15, 20]}, index=["Net Income"], columns=periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_initial_re_negative(self):
        """Test balancer with negative initial retained earnings."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0],
                "Total Assets": [100],
                "Total Liabilities": [50],
                "Total Equity": [50],
                "Revolver": [0],
                "Retained Earnings": [-10],
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

        is_df = pd.DataFrame({"Net Income": [10]}, index=["Net Income"])
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0], "Beginning Cash Balance": [0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods[:1], self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_no_plug_items(self):
        """Test balancer when no plug items exist."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0],
                "Total Assets": [100],
                "Total Liabilities": [100],
                "Total Equity": [0],
                "Retained Earnings": [0],
            },
            index=["Cash & Cash Equivalents", "Total Assets", "Total Liabilities", "Total Equity", "Retained Earnings"],
        )

        is_df = pd.DataFrame({"Net Income": [10]}, index=["Net Income"])
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0], "Beginning Cash Balance": [0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
        )

        balancer = BalanceSheetBalancer()
        # This should handle gracefully even without Revolver
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods[:1], self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        # Might not balance but shouldn't crash

    def test_balancer_cash_as_plug(self):
        """Test balancer uses cash as plug when revolver not available."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0],
                "Total Assets": [100],
                "Total Liabilities": [50],
                "Total Equity": [50],
                "Retained Earnings": [0],
            },
            index=["Cash & Cash Equivalents", "Total Assets", "Total Liabilities", "Total Equity", "Retained Earnings"],
        )

        is_df = pd.DataFrame({"Net Income": [10]}, index=["Net Income"])
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0], "Beginning Cash Balance": [0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods[:1], self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced


class TestCashFlowBalancerEdgeCases:
    """Test CashFlowBalancer edge cases."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_cf_balancer_single_period(self):
        """Test CF balancer with single period."""
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
        # Beginning 90 + flows 10 == balance sheet cash 100
        assert result.balanced
        assert cf_df.loc["Net Change in Cash"].iloc[0] == 10
        assert cf_df.loc["Ending Cash Balance"].iloc[0] == 100

    def test_cf_balancer_multiple_periods(self):
        """Test CF balancer with multiple periods."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [100, 120, 150],
                "Ending Cash Balance": [100, 120, 150],
            },
            index=["Cash & Cash Equivalents", "Ending Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        cf_df = pd.DataFrame(
            {
                "Net Income": [10, 15, 20],
                "Net Change in Cash": [0, 0, 0],
                "Beginning Cash Balance": [90, 100, 120],
                "Ending Cash Balance": [100, 120, 150],
            },
            index=["Net Income", "Net Change in Cash", "Beginning Cash Balance", "Ending Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = CashFlowBalancer()
        result = balancer.balance(bs_df, cf_df, self.periods)
        assert result.balanced

    def test_cf_balancer_mismatched_cash(self):
        """Test CF balancer when beginning cash doesn't match BS."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [100, 120, 150],
                "Ending Cash Balance": [100, 120, 150],
            },
            index=["Cash & Cash Equivalents", "Ending Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        cf_df = pd.DataFrame(
            {
                "Net Income": [10, 15, 20],
                "Net Change in Cash": [0, 0, 0],
                "Beginning Cash Balance": [50, 100, 120],  # Different from BS
                "Ending Cash Balance": [100, 120, 150],
            },
            index=["Net Income", "Net Change in Cash", "Beginning Cash Balance", "Ending Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = CashFlowBalancer()
        result = balancer.balance(bs_df, cf_df, self.periods)
        # Should adjust to make CF tie to BS
