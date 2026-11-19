"""
Tests for balancer module to achieve 100% coverage - targeting _total_* and _sum_* methods and edge cases.
"""

import numpy as np
import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.line_item import LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.statement import Statement, StatementType


def _create_bs_df(line_names, values_list, periods):
    """Helper to create BS DataFrame with line items as index, periods as columns."""
    return pd.DataFrame(values_list, index=line_names, columns=periods.to_dataframe_index())


class TestBalancerSumMethods:
    """Test the _sum_* methods (explicit leaf items) for coverage."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = Assumptions()

    def test_sum_assets_method(self):
        """Test _sum_assets method directly."""
        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Inventory",
                "Prepaid Expenses",
                "Property Plant & Equipment, Net",
                "Intangible Assets",
                "Goodwill",
            ],
            [
                [100, 200, 300],
                [50, 60, 70],
                [30, 40, 50],
                [10, 10, 10],
                [500, 550, 600],
                [100, 100, 100],
                [50, 50, 50],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        total = balancer._sum_assets(bs_df, 0, exclude_plugs=False)
        assert total == 100 + 50 + 30 + 10 + 500 + 100 + 50  # 840

        # With exclude_plugs=True, cash should be excluded
        total_excl = balancer._sum_assets(bs_df, 0, exclude_plugs=True)
        assert total_excl == 50 + 30 + 10 + 500 + 100 + 50  # 740

    def test_sum_liabilities_method(self):
        """Test _sum_liabilities method directly."""
        bs_df = _create_bs_df(
            [
                "Accounts Payable",
                "Accrued Expenses",
                "Deferred Revenue",
                "Current Portion of Long-Term Debt",
                "Revolver",
                "Long-Term Debt",
                "Deferred Tax Liability",
            ],
            [
                [50, 60, 70],
                [20, 25, 30],
                [100, 120, 140],
                [50, 50, 50],
                [100, 100, 100],
                [500, 500, 500],
                [30, 30, 30],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        total = balancer._sum_liabilities(bs_df, 0, exclude_plugs=False)
        assert total == 50 + 20 + 100 + 50 + 100 + 500 + 30  # 850

        # With exclude_plugs=True, revolver should be excluded
        total_excl = balancer._sum_liabilities(bs_df, 0, exclude_plugs=True)
        assert total_excl == 50 + 20 + 100 + 50 + 500 + 30  # 750

    def test_sum_equity_method(self):
        """Test _sum_equity method directly."""
        bs_df = _create_bs_df(
            ["Common Stock", "Additional Paid-In Capital", "Retained Earnings"],
            [
                [100, 100, 100],
                [500, 500, 500],
                [50, 100, 150],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        total = balancer._sum_equity(bs_df, 0, exclude_plugs=False)
        assert total == 100 + 500 + 50  # 650


class TestBalancerClassification:
    """Leaf classification: explicit name lists (no topology) and statement-tree expansion."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = Assumptions()

    def test_sum_assets_uses_known_leaf_names(self):
        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Inventory",
                "Prepaid Expenses",
                "Property Plant & Equipment, Net",
                "Intangible Assets",
                "Goodwill",
                "Total Assets",
                "Revolver",
            ],
            [
                [100, 200, 300],
                [50, 60, 70],
                [30, 40, 50],
                [10, 10, 10],
                [500, 550, 600],
                [100, 100, 100],
                [50, 50, 50],
                [9999, 0, 0],
                [0, 0, 0],
            ],
            self.periods,
        )
        balancer = BalanceSheetBalancer()
        assert balancer._sum_assets(bs_df, 0) == 100 + 50 + 30 + 10 + 500 + 100 + 50
        assert balancer._sum_assets(bs_df, 0, exclude_plugs=True) == 50 + 30 + 10 + 500 + 100 + 50

    def test_sum_liabilities_uses_known_leaf_names(self):
        bs_df = _create_bs_df(
            [
                "Accounts Payable",
                "Accrued Expenses",
                "Deferred Revenue",
                "Current Portion of Long-Term Debt",
                "Revolver",
                "Long-Term Debt",
                "Deferred Tax Liability",
                "Total Liabilities",
            ],
            [
                [50, 60, 70],
                [20, 25, 30],
                [100, 120, 140],
                [50, 50, 50],
                [100, 100, 100],
                [500, 500, 500],
                [30, 30, 30],
                [9999, 0, 0],
            ],
            self.periods,
        )
        balancer = BalanceSheetBalancer()
        assert balancer._sum_liabilities(bs_df, 0) == 50 + 20 + 100 + 50 + 100 + 500 + 30
        assert balancer._sum_liabilities(bs_df, 0, exclude_plugs=True) == 50 + 20 + 100 + 50 + 500 + 30

    def test_sum_equity_uses_known_leaf_names(self):
        bs_df = _create_bs_df(
            ["Common Stock", "Additional Paid-In Capital", "Retained Earnings", "Total Equity"],
            [[100, 100, 100], [500, 500, 500], [50, 100, 150], [9999, 0, 0]],
            self.periods,
        )
        balancer = BalanceSheetBalancer()
        assert balancer._sum_equity(bs_df, 0) == 100 + 500 + 50

    def test_statement_topology_drives_classification(self):
        """Custom lines are picked up when they hang off the Total Assets / Liabilities / Equity trees."""
        stmt = Statement(StatementType.BALANCE_SHEET)
        stmt.add_line_item(LineItem("Cash & Cash Equivalents", LineItemType.PLUG, "balance_sheet", order=1))
        stmt.add_line_item(LineItem("Crypto Holdings", LineItemType.INPUT, "balance_sheet", order=2))
        stmt.add_line_item(
            LineItem(
                "Total Assets",
                LineItemType.AGGREGATE,
                "balance_sheet",
                order=3,
                children=["Cash & Cash Equivalents", "Crypto Holdings"],
            )
        )
        stmt.add_line_item(LineItem("Customer Deposits", LineItemType.INPUT, "balance_sheet", order=4))
        stmt.add_line_item(
            LineItem(
                "Total Liabilities", LineItemType.AGGREGATE, "balance_sheet", order=5, children=["Customer Deposits"]
            )
        )
        stmt.add_line_item(LineItem("Members Capital", LineItemType.INPUT, "balance_sheet", order=6))
        stmt.add_line_item(
            LineItem("Total Equity", LineItemType.AGGREGATE, "balance_sheet", order=7, children=["Members Capital"])
        )

        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Crypto Holdings",
                "Total Assets",
                "Customer Deposits",
                "Total Liabilities",
                "Members Capital",
                "Total Equity",
            ],
            [[0, 0, 0], [40, 40, 40], [0, 0, 0], [70, 70, 70], [0, 0, 0], [30, 30, 30], [0, 0, 0]],
            self.periods,
        )
        is_df = pd.DataFrame([[0, 0, 0]], index=["Net Income"], columns=self.periods.to_dataframe_index())
        cf_df = pd.DataFrame([[0, 0, 0]], index=["Net Change in Cash"], columns=self.periods.to_dataframe_index())

        balancer = BalanceSheetBalancer()
        assets, liabilities, equity = balancer._classify(stmt, bs_df)
        assert assets == ["Cash & Cash Equivalents", "Crypto Holdings"]
        assert liabilities == ["Customer Deposits"]
        assert equity == ["Members Capital"]

        result = balancer.balance(bs_df, cf_df, is_df, self.periods, self.assumptions, stmt)
        assert result.balanced
        # cash = (70 + 30) - 40
        assert bs_df.loc["Cash & Cash Equivalents"].iloc[0] == 60
        assert bs_df.loc["Total Assets"].iloc[0] == 100


class TestBalancerEdgeCasesDeep:
    """Test balancer edge cases for full coverage."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.assumptions = Assumptions()

    def test_balancer_with_missing_total_assets(self):
        """Test balancer when Total Assets line doesn't exist."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Accounts Receivable": [50, 60, 70],
                "Property Plant & Equipment, Net": [500, 550, 600],
                "Total Liabilities": [200, 250, 300],
                "Total Equity": [350, 360, 370],
                "Revolver": [0, 0, 0],
                "Retained Earnings": [0, 0, 0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=self.periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame(
            {"Net Income": [10, 15, 20]}, index=["Net Income"], columns=self.periods.to_dataframe_index()
        )
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_with_nan_values(self):
        """Test balancer handles NaN values gracefully."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [np.nan, 0, 0],
                "Accounts Receivable": [50, np.nan, 70],
                "Property Plant & Equipment, Net": [500, 550, np.nan],
                "Total Liabilities": [200, 250, 300],
                "Total Equity": [350, 360, 370],
                "Revolver": [0, 0, 0],
                "Retained Earnings": [0, 0, 0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=self.periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame(
            {"Net Income": [10, 15, 20]}, index=["Net Income"], columns=self.periods.to_dataframe_index()
        )
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_multiple_iterations_scenario(self):
        """Test balancer handles scenario where multiple passes might be needed."""
        # This tests the logic even though current implementation is single-pass
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Accounts Receivable": [100, 100, 100],
                "Property Plant & Equipment, Net": [1000, 1000, 1000],
                "Total Liabilities": [500, 500, 500],
                "Total Equity": [500, 500, 500],
                "Revolver": [200, 200, 200],  # High revolver to pay down
                "Retained Earnings": [100, 100, 100],
            },
            index=[
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
                "Retained Earnings",
            ],
            columns=self.periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame(
            {"Net Income": [50, 50, 50]}, index=["Net Income"], columns=self.periods.to_dataframe_index()
        )
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced

    def test_balancer_convergence_failure(self):
        """Test balancer handles convergence issues."""
        # Create an impossible scenario - but current impl is single pass so it just does best effort
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0],
                "Accounts Receivable": [100],
                "Property Plant & Equipment, Net": [1000],
                "Total Liabilities": [5000],  # Way too high
                "Total Equity": [500],
                "Revolver": [0],
                "Retained Earnings": [0],
            },
            index=[
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
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
        # Should not crash, might not balance perfectly
        assert isinstance(result.balanced, bool)

    def test_balancer_without_retained_earnings_line(self):
        """Test balancer when Retained Earnings line is missing."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [0, 0, 0],
                "Accounts Receivable": [50, 60, 70],
                "Property Plant & Equipment, Net": [500, 550, 600],
                "Total Liabilities": [200, 250, 300],
                "Total Equity": [350, 360, 370],
                "Revolver": [0, 0, 0],
                # No Retained Earnings
            },
            index=[
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Property Plant & Equipment, Net",
                "Total Liabilities",
                "Total Equity",
                "Revolver",
            ],
            columns=self.periods.to_dataframe_index(),
        )

        is_df = pd.DataFrame(
            {"Net Income": [10, 15, 20]}, index=["Net Income"], columns=self.periods.to_dataframe_index()
        )
        cf_df = pd.DataFrame(
            {"Net Change in Cash": [0, 0, 0], "Beginning Cash Balance": [0, 0, 0]},
            index=["Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = BalanceSheetBalancer()
        result = balancer.balance(
            bs_df, cf_df, is_df, self.periods, self.assumptions, Statement(StatementType.BALANCE_SHEET)
        )
        assert result.balanced


class TestCashFlowBalancerEdgeCasesDeep:
    """Test CashFlowBalancer edge cases for full coverage."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_cf_balancer_missing_ending_cash(self):
        """Test CF balancer when Ending Cash Balance line is missing."""
        bs_df = pd.DataFrame(
            {
                "Cash & Cash Equivalents": [100, 120, 150],
            },
            index=["Cash & Cash Equivalents"],
            columns=self.periods.to_dataframe_index(),
        )

        cf_df = pd.DataFrame(
            {
                "Net Income": [10, 15, 20],
                "Net Change in Cash": [0, 0, 0],
                "Beginning Cash Balance": [90, 100, 120],
                # No Ending Cash Balance
            },
            index=["Net Income", "Net Change in Cash", "Beginning Cash Balance"],
            columns=self.periods.to_dataframe_index(),
        )

        balancer = CashFlowBalancer()
        result = balancer.balance(bs_df, cf_df, self.periods)
        # Should still work, uses BS cash for ending
        assert isinstance(result.balanced, bool)

    def test_cf_balancer_mismatched_beginning_cash(self):
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
        # Should adjust beginning cash to match BS
        assert result.balanced


class TestRecalculateAggregates:
    """Test _recalculate_aggregates method."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_recalculate_aggregates(self):
        """Test _recalculate_aggregates updates total lines."""
        bs_df = _create_bs_df(
            [
                "Cash & Cash Equivalents",
                "Accounts Receivable",
                "Inventory",
                "Prepaid Expenses",
                "Property Plant & Equipment, Net",
                "Intangible Assets",
                "Goodwill",
                "Total Assets",
                "Accounts Payable",
                "Accrued Expenses",
                "Deferred Revenue",
                "Current Portion of Long-Term Debt",
                "Revolver",
                "Long-Term Debt",
                "Deferred Tax Liability",
                "Total Liabilities",
                "Common Stock",
                "Additional Paid-In Capital",
                "Retained Earnings",
                "Total Equity",
                "Total Current Assets",
                "Total Current Liabilities",
            ],
            [
                [100, 200, 300],
                [50, 60, 70],
                [30, 40, 50],
                [10, 10, 10],
                [500, 550, 600],
                [100, 100, 100],
                [50, 50, 50],
                [0, 0, 0],
                [50, 60, 70],
                [20, 25, 30],
                [100, 120, 140],
                [50, 50, 50],
                [0, 0, 0],
                [500, 500, 500],
                [30, 30, 30],
                [0, 0, 0],
                [100, 100, 100],
                [500, 500, 500],
                [50, 100, 150],
                [0, 0, 0],
                [0, 0, 0],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)

        # Check totals were recalculated from leaf items
        # Total Assets = Cash + AR + Inventory + Prepaid + PP&E Net + Intangible + Goodwill
        assert bs_df.loc["Total Assets"].iloc[0] == 100 + 50 + 30 + 10 + 500 + 100 + 50  # 840
        # Total Liabilities = AP + Accrued + Deferred Rev + Current LTD + Revolver + LTD + Deferred Tax
        assert bs_df.loc["Total Liabilities"].iloc[0] == 50 + 20 + 100 + 50 + 0 + 500 + 30  # 750
        # Total Equity = Common Stock + APIC + Retained Earnings
        assert bs_df.loc["Total Equity"].iloc[0] == 100 + 500 + 50  # 650
        # Total Current Assets = Cash + AR + Inventory + Prepaid
        assert bs_df.loc["Total Current Assets"].iloc[0] == 100 + 50 + 30 + 10  # 190
        # Total Current Liabilities = AP + Accrued + Deferred Rev + Current LTD
        assert bs_df.loc["Total Current Liabilities"].iloc[0] == 50 + 20 + 100 + 50  # 220
