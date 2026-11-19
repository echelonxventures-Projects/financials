"""
Tests for balancer line 220 - Total Current Liabilities recalculation.
This test specifically targets line 220 in balancer.py which is in _recalculate_aggregates.
"""
from __future__ import annotations

import pandas as pd

from financial_model.core.balancer import BalanceSheetBalancer
from financial_model.core.period import Period, PeriodIndex, PeriodType


def _create_bs_df(line_names, values_list, periods):
    """Helper to create BS DataFrame."""
    return pd.DataFrame(values_list, index=line_names, columns=periods.to_dataframe_index())


class TestBalancerLine220Coverage:
    """Test _recalculate_aggregates Total Current Liabilities (line 220)."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_recalculate_total_current_liabilities_all_periods(self):
        """Test Total Current Liabilities recalculation across all periods (line 220)."""
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
        assert bs_df.loc["Total Current Liabilities"].iloc[0] == 220  # 50+20+100+50
        assert bs_df.loc["Total Current Liabilities"].iloc[1] == 255  # 60+25+120+50
        assert bs_df.loc["Total Current Liabilities"].iloc[2] == 290  # 70+30+140+50

    def test_recalculate_total_current_assets(self):
        """Test Total Current Assets recalculation (lines 307-315)."""
        bs_df = _create_bs_df(
            ["Cash & Cash Equivalents", "Accounts Receivable", "Inventory", "Prepaid Expenses", "Total Current Assets"],
            [
                [100, 150, 200],
                [50, 60, 70],
                [30, 40, 50],
                [10, 15, 20],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)
        # Total Current Assets = Cash + AR + Inventory + Prepaid
        assert bs_df.loc["Total Current Assets"].iloc[0] == 190
        assert bs_df.loc["Total Current Assets"].iloc[1] == 265
        assert bs_df.loc["Total Current Assets"].iloc[2] == 340

    def test_recalculate_total_assets(self):
        """Test Total Assets recalculation (lines 291-294)."""
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
            ],
            [
                [100, 150, 200],
                [50, 60, 70],
                [30, 40, 50],
                [10, 15, 20],
                [500, 550, 600],
                [200, 220, 240],
                [100, 110, 120],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)
        # Total Assets = sum of all asset leaf items (explicit list in _sum_assets)
        # Period 0: 100+50+30+10+500+200+100 = 990
        # Period 1: 150+60+40+15+550+220+110 = 1145
        # Period 2: 200+70+50+20+600+240+120 = 1300
        assert bs_df.loc["Total Assets"].iloc[0] == 990
        assert bs_df.loc["Total Assets"].iloc[1] == 1145
        assert bs_df.loc["Total Assets"].iloc[2] == 1300

    def test_recalculate_total_liabilities(self):
        """Test Total Liabilities recalculation (lines 296-299)."""
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
                [200, 150, 100],
                [300, 280, 260],
                [40, 45, 50],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)
        # Total Liabilities = sum of all liability leaf items
        assert bs_df.loc["Total Liabilities"].iloc[0] == 760
        assert bs_df.loc["Total Liabilities"].iloc[1] == 730
        assert bs_df.loc["Total Liabilities"].iloc[2] == 700

    def test_recalculate_total_equity(self):
        """Test Total Equity recalculation (lines 301-304)."""
        bs_df = _create_bs_df(
            ["Common Stock", "Additional Paid-In Capital", "Retained Earnings", "Total Equity"],
            [
                [100, 100, 100],
                [500, 500, 500],
                [200, 250, 300],
                [0, 0, 0],
            ],
            self.periods,
        )

        balancer = BalanceSheetBalancer()
        balancer._recalculate_aggregates(bs_df)
        # Total Equity = sum of all equity leaf items
        assert bs_df.loc["Total Equity"].iloc[0] == 800
        assert bs_df.loc["Total Equity"].iloc[1] == 850
        assert bs_df.loc["Total Equity"].iloc[2] == 900
