"""
Tests for balancer line 220 - Total Current Liabilities recalculation.
"""
from __future__ import annotations

import pandas as pd

from financial_model.core.balancer import BalanceSheetBalancer
from financial_model.core.period import Period, PeriodIndex, PeriodType


def _create_bs_df(line_names, values_list, periods):
    """Helper to create BS DataFrame."""
    return pd.DataFrame(values_list, index=line_names, columns=periods.to_dataframe_index())


class TestBalancerLine220:
    """Test _recalculate_aggregates Total Current Liabilities (line 220)."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_recalculate_total_current_liabilities(self):
        """Test Total Current Liabilities recalculation (line 220 area)."""
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
