"""
Tests for balancer - targeting revolver paydown and draw branches.
"""
from __future__ import annotations

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.statement import Statement, StatementType


def _create_bs_df(line_names, values_list, periods):
    """Helper to create BS DataFrame."""
    return pd.DataFrame(values_list, index=line_names, columns=periods.to_dataframe_index())


class TestBalancerRevolverBranches:
    """Test revolver paydown and draw branches in balancer."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = Assumptions()

    def test_revolver_untouched_when_cash_is_sufficient(self):
        """Implied cash above min_cash: the revolver is zero and cash absorbs the surplus."""
        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Accounts Payable",
                "Long-Term Debt",
                "Common Stock",
                "Revolver",
                "Retained Earnings",
            ],
            [
                [0, 0, 0],
                [100, 60, 70],
                [500, 550, 600],
                [200, 250, 300],
                [350, 360, 370],
                [100, 100, 100],
                [100, 100, 100],  # stale value must be overwritten
                [0, 0, 0],
            ],
            self.periods,
        )
        is_df = pd.DataFrame([[10, 15, 20]], index=["Net Income"], columns=self.periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            [[0, 0, 0], [0, 0, 0]],
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced
        # implied cash = (200 + 350) + (100 + 10) - (100 + 500) = 60
        assert result.plug_values["Revolver_0"] == 0
        assert abs(result.plug_values["Cash & Cash Equivalents_0"] - 60) < 1e-9

    def test_revolver_drawn_when_cash_would_go_negative(self):
        """Assets exceed liabilities + equity: cash is floored at min_cash and the revolver funds the gap."""
        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Accounts Payable",
                "Long-Term Debt",
                "Common Stock",
                "Additional Paid-In Capital",
                "Retained Earnings",
                "Revolver",
            ],
            [
                [0, 0, 0],
                [50, 60, 70],
                [2000, 2050, 2100],
                [200, 250, 300],
                [500, 500, 500],
                [100, 100, 100],
                [500, 500, 500],
                [0, 0, 0],
                [0, 0, 0],
            ],
            self.periods,
        )
        is_df = pd.DataFrame([[10, 15, 20]], index=["Net Income"], columns=self.periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            [[0, 0, 0], [0, 0, 0]],
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        from financial_model.core.assumptions import Driver

        assumptions = Assumptions(drivers={"min_cash": Driver("min_cash", 25, "Min cash", "$")})
        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced
        # implied cash = (200 + 500) + (100 + 500 + 10) - (50 + 2000) = -740 -> cash 25, revolver 765
        assert abs(result.plug_values["Cash & Cash Equivalents_0"] - 25) < 1e-9
        assert abs(result.plug_values["Revolver_0"] - 765) < 1e-9

    def test_revolver_only_when_no_cash_line(self):
        """Without a cash line the revolver alone absorbs a funding gap."""
        bs_df = _create_bs_df(
            [
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Accounts Payable",
                "Long-Term Debt",
                "Common Stock",
                "Additional Paid-In Capital",
                "Retained Earnings",
                "Revolver",
            ],
            [
                [50, 60, 70],
                [2000, 2050, 2100],
                [200, 250, 300],
                [500, 500, 500],
                [100, 100, 100],
                [500, 500, 500],
                [0, 0, 0],
                [0, 0, 0],
            ],
            self.periods,
        )
        is_df = pd.DataFrame([[10, 15, 20]], index=["Net Income"], columns=self.periods.to_dataframe_index())
        cf_df = pd.DataFrame(
            [[0, 0, 0], [0, 0, 0]],
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer(cash_line="NonExistentCash")
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced
        assert abs(result.plug_values["Revolver_0"] - 740) < 1e-9

    def test_recalculate_aggregates_line_220(self):
        """Test _recalculate_aggregates Total Current Liabilities (line 220 area)."""
        bs_df = _create_bs_df(
            [
                "Accounts Payable",
                "Accrued Expenses",
                "Deferred Revenue",
                "Current Portion of Long-Term Debt",
                "Total Current Liabilities",
            ],
            [
                [50, 60, 70],
                [20, 25, 30],
                [100, 120, 140],
                [50, 50, 50],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)
        # Total Current Liabilities = AP + Accrued + Deferred Rev + Current LTD
        assert bs_df.loc["Total Current Liabilities"].iloc[0] == 50 + 20 + 100 + 50  # 220
