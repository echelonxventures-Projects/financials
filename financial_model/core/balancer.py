"""
Balancing logic for Balance Sheet and Cash Flow Statement.

Balance sheet plugs
-------------------
* **Retained Earnings** rolls forward from the opening balance with net income
  less dividends (it is derived, not solved).
* **Cash** and **Revolver** are solved jointly each period so that
  ``Assets == Liabilities + Equity``: the implied cash balance is
  ``(Liabilities ex-revolver + Equity) - (Assets ex-cash)``. If that is at least the
  ``min_cash`` driver the revolver is zero; otherwise cash is held at ``min_cash``
  and the shortfall is drawn on the revolver.

Cash flow check
---------------
``Net Change in Cash`` is set to the balance-sheet cash movement and the
statement is considered tied when the sum of its operating, investing and
financing flows equals that movement.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.line_item import LineItemType
from financial_model.core.period import PeriodIndex
from financial_model.core.statement import Statement


@dataclass
class BalanceResult:
    balanced: bool
    iterations: int
    max_diff: float
    plug_values: dict[str, float]
    errors: list[str]


# Fallback classification used when no Statement topology is available
DEFAULT_ASSET_LINES = [
    "Cash & Cash Equivalents",
    "Accounts Receivable",
    "Inventory",
    "Prepaid Expenses",
    "Property Plant & Equipment, Net",
    "Intangible Assets",
    "Goodwill",
]
DEFAULT_LIABILITY_LINES = [
    "Accounts Payable",
    "Accrued Expenses",
    "Deferred Revenue",
    "Current Portion of Long-Term Debt",
    "Revolver",
    "Long-Term Debt",
    "Term Loan A",
    "Term Loan B",
    "Subordinated Debt",
    "Mezzanine Debt",
    "Deferred Tax Liability",
]
DEFAULT_EQUITY_LINES = [
    "Common Stock",
    "Additional Paid-In Capital",
    "Retained Earnings",
]
DEFAULT_CURRENT_ASSET_LINES = ["Cash & Cash Equivalents", "Accounts Receivable", "Inventory", "Prepaid Expenses"]
DEFAULT_CURRENT_LIABILITY_LINES = [
    "Accounts Payable",
    "Accrued Expenses",
    "Deferred Revenue",
    "Current Portion of Long-Term Debt",
]


def _cell(df: pd.DataFrame, line: str, period_idx: int) -> float:
    if line not in df.index:
        return 0.0
    val = df.loc[line].iloc[period_idx]
    return 0.0 if pd.isna(val) else float(val)


class BalanceSheetBalancer:
    """
    Balances the balance sheet by solving for plug items.
    Standard plugs: Cash (asset), Revolver (liability); Retained Earnings is derived from NI.
    """

    def __init__(
        self,
        cash_line: str = "Cash & Cash Equivalents",
        revolver_line: str = "Revolver",
        retained_earnings_line: str = "Retained Earnings",
        max_iterations: int = 100,
        tolerance: float = 1e-6,
    ):
        self.cash_line = cash_line
        self.revolver_line = revolver_line
        self.retained_earnings_line = retained_earnings_line
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.opening_retained_earnings = 0.0

    # ------------------------------------------------------------------ #
    # Classification helpers
    # ------------------------------------------------------------------ #
    def _leaves(self, stmt: Statement | None, total_line: str, fallback: list[str], bs_data: pd.DataFrame) -> list[str]:
        if stmt is not None and total_line in stmt.line_items:
            leaves = [name for name in stmt.leaf_lines(total_line) if name in bs_data.index]
            if leaves:
                return leaves
        return [name for name in fallback if name in bs_data.index]

    def _classify(self, stmt: Statement | None, bs_data: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
        assets = self._leaves(stmt, "Total Assets", DEFAULT_ASSET_LINES, bs_data)
        liabilities = self._leaves(stmt, "Total Liabilities", DEFAULT_LIABILITY_LINES, bs_data)
        equity = self._leaves(stmt, "Total Equity", DEFAULT_EQUITY_LINES, bs_data)
        return assets, liabilities, equity

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def balance(
        self,
        bs_data: pd.DataFrame,
        cf_data: pd.DataFrame,
        is_data: pd.DataFrame,
        periods: PeriodIndex,
        assumptions: Assumptions,
        balance_sheet_stmt: Statement | None = None,
    ) -> BalanceResult:
        """Balance BS: Assets = Liabilities + Equity by solving cash / revolver per period."""
        errors: list[str] = []
        plug_values: dict[str, float] = {}

        assets, liabilities, equity = self._classify(balance_sheet_stmt, bs_data)
        has_cash = self.cash_line in bs_data.index
        has_revolver = self.revolver_line in bs_data.index

        for period_idx in range(len(periods)):
            col = bs_data.columns[period_idx]

            # Retained earnings roll-forward (derived, not a plug)
            if self.retained_earnings_line in bs_data.index:
                ni = _cell(is_data, "Net Income", period_idx)
                dividends = _cell(cf_data, "Dividends Paid", period_idx)  # negative = cash out
                if "Dividends" in is_data.index:
                    dividends -= abs(_cell(is_data, "Dividends", period_idx))
                prev_re = (
                    _cell(bs_data, self.retained_earnings_line, period_idx - 1)
                    if period_idx > 0
                    else self.opening_retained_earnings
                )
                bs_data.loc[self.retained_earnings_line, col] = prev_re + ni + dividends

            assets_excl = sum(_cell(bs_data, line, period_idx) for line in assets if line != self.cash_line)
            liab_excl = sum(_cell(bs_data, line, period_idx) for line in liabilities if line != self.revolver_line)
            equity_total = sum(_cell(bs_data, line, period_idx) for line in equity)

            # Cash needed so that assets == liabilities + equity (before any revolver draw)
            implied_cash = liab_excl + equity_total - assets_excl
            min_cash = assumptions.get_value("min_cash", period_idx) if assumptions is not None else 0.0

            if has_cash and has_revolver:
                cash = max(min_cash, implied_cash)
                revolver = cash - implied_cash
            elif has_cash:
                cash = implied_cash
                revolver = 0.0
                if implied_cash < 0:
                    errors.append(
                        f"Period {period_idx}: implied cash is negative ({implied_cash:,.0f}) and no revolver line exists"
                    )
            elif has_revolver:
                cash = 0.0
                revolver = max(0.0, -implied_cash)
            else:
                cash = revolver = 0.0

            if has_cash:
                bs_data.loc[self.cash_line, col] = cash
                plug_values[f"{self.cash_line}_{period_idx}"] = cash
            if has_revolver:
                bs_data.loc[self.revolver_line, col] = revolver
                plug_values[f"{self.revolver_line}_{period_idx}"] = revolver

        self._recalculate_aggregates(bs_data, balance_sheet_stmt)

        max_diff = 0.0
        for period_idx in range(len(periods)):
            if "Total Assets" in bs_data.index:
                total_assets = _cell(bs_data, "Total Assets", period_idx)
            else:
                total_assets = sum(_cell(bs_data, line, period_idx) for line in assets)
            if "Total Liabilities" in bs_data.index and "Total Equity" in bs_data.index:
                le = _cell(bs_data, "Total Liabilities", period_idx) + _cell(bs_data, "Total Equity", period_idx)
            else:
                le = sum(_cell(bs_data, line, period_idx) for line in liabilities) + sum(
                    _cell(bs_data, line, period_idx) for line in equity
                )
            max_diff = max(max_diff, abs(total_assets - le))

        return BalanceResult(
            balanced=max_diff < max(self.tolerance, 1e-6),
            iterations=1,
            max_diff=max_diff,
            plug_values=plug_values,
            errors=errors,
        )

    # ------------------------------------------------------------------ #
    # Aggregates
    # ------------------------------------------------------------------ #
    def _recalculate_aggregates(self, bs_data: pd.DataFrame, stmt: Statement | None = None) -> None:
        """Recalculate aggregate lines (Total Assets, Total Liabilities, Total Equity, current subtotals)."""
        if stmt is not None and stmt.aggregate_items():
            for item in stmt.aggregate_items():
                if item.name not in bs_data.index:
                    continue
                for period_idx in range(bs_data.shape[1]):
                    bs_data.loc[item.name, bs_data.columns[period_idx]] = item.calculate(bs_data, period_idx, {})
            return

        groups = {
            "Total Current Assets": DEFAULT_CURRENT_ASSET_LINES,
            "Total Current Liabilities": DEFAULT_CURRENT_LIABILITY_LINES,
            "Total Assets": DEFAULT_ASSET_LINES,
            "Total Liabilities": DEFAULT_LIABILITY_LINES,
            "Total Equity": DEFAULT_EQUITY_LINES,
        }
        for period_idx in range(bs_data.shape[1]):
            col = bs_data.columns[period_idx]
            for total_line, members in groups.items():
                if total_line in bs_data.index:
                    bs_data.loc[total_line, col] = sum(_cell(bs_data, line, period_idx) for line in members)

    # Backwards-compatible helpers -------------------------------------- #
    def _sum_assets(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        return sum(
            _cell(bs_data, line, period_idx)
            for line in DEFAULT_ASSET_LINES
            if not (exclude_plugs and line == self.cash_line)
        )

    def _sum_liabilities(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        return sum(
            _cell(bs_data, line, period_idx)
            for line in DEFAULT_LIABILITY_LINES
            if not (exclude_plugs and line == self.revolver_line)
        )

    def _sum_equity(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:  # noqa: ARG002
        return sum(_cell(bs_data, line, period_idx) for line in DEFAULT_EQUITY_LINES)


class CashFlowBalancer:
    """
    Ties the cash flow statement to the balance sheet:
    ``Beginning Cash + Net Change in Cash == BS Cash`` and the net change equals
    the sum of operating, investing and financing flows.
    """

    SUBTOTALS = ("Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow")

    def __init__(
        self,
        cash_line: str = "Cash & Cash Equivalents",
        net_change_line: str = "Net Change in Cash",
        beginning_cash_line: str = "Beginning Cash Balance",
        ending_cash_line: str = "Ending Cash Balance",
        tolerance: float = 1e-6,
    ):
        self.cash_line = cash_line
        self.net_change_line = net_change_line
        self.beginning_cash_line = beginning_cash_line
        self.ending_cash_line = ending_cash_line
        self.tolerance = tolerance

    def _flow_lines(self, cf_data: pd.DataFrame, stmt: Statement | None) -> list[str]:
        """Lines whose sum should equal the net change in cash."""
        if all(line in cf_data.index for line in self.SUBTOTALS):
            return list(self.SUBTOTALS)
        skip = {self.net_change_line, self.beginning_cash_line, self.ending_cash_line, *self.SUBTOTALS}
        if stmt is not None and stmt.line_items:
            return [
                item.name
                for item in stmt.ordered_items()
                if item.name in cf_data.index and item.name not in skip and item.line_type != LineItemType.AGGREGATE
            ]
        return [line for line in cf_data.index if line not in skip]

    def balance(
        self,
        bs_data: pd.DataFrame,
        cf_data: pd.DataFrame,
        periods: PeriodIndex,
        cash_flow_stmt: Statement | None = None,
    ) -> BalanceResult:
        errors: list[str] = []
        plug_values: dict[str, float] = {}
        flow_lines = self._flow_lines(cf_data, cash_flow_stmt)
        max_diff = 0.0

        for period_idx in range(len(periods)):
            col = cf_data.columns[period_idx]
            ending_cash = _cell(bs_data, self.cash_line, period_idx)

            if period_idx == 0:
                if self.beginning_cash_line in cf_data.index and pd.notna(
                    cf_data.loc[self.beginning_cash_line].iloc[0]
                ):
                    beginning_cash = float(cf_data.loc[self.beginning_cash_line].iloc[0])
                else:
                    beginning_cash = ending_cash
            else:
                beginning_cash = _cell(bs_data, self.cash_line, period_idx - 1)

            required_change = ending_cash - beginning_cash
            flows = sum(_cell(cf_data, line, period_idx) for line in flow_lines)

            if self.net_change_line in cf_data.index:
                cf_data.loc[self.net_change_line, col] = required_change
            if self.beginning_cash_line in cf_data.index:
                cf_data.loc[self.beginning_cash_line, col] = beginning_cash
            if self.ending_cash_line in cf_data.index:
                cf_data.loc[self.ending_cash_line, col] = beginning_cash + required_change

            diff = abs(flows - required_change)
            plug_values[f"{self.net_change_line}_{period_idx}"] = required_change - flows
            if diff > self.tolerance:
                errors.append(
                    f"Period {period_idx}: cash flows sum to {flows:,.2f} but balance sheet cash moved {required_change:,.2f}"
                )
            max_diff = max(max_diff, diff)

        return BalanceResult(
            balanced=max_diff < max(self.tolerance, 1e-6),
            iterations=1,
            max_diff=max_diff,
            plug_values=plug_values,
            errors=errors,
        )
