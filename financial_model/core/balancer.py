"""
Balancing logic for Balance Sheet and Cash Flow Statement.
Handles plugs: cash, revolver, retained earnings.
"""

from dataclasses import dataclass
from typing import Any
import pandas as pd
import numpy as np

from financial_model.core.statement import Statement, StatementType
from financial_model.core.line_item import LineItem, LineItemType
from financial_model.core.period import PeriodIndex
from financial_model.core.assumptions import Assumptions


@dataclass
class BalanceResult:
    balanced: bool
    iterations: int
    max_diff: float
    plug_values: dict[str, float]
    errors: list[str]


class BalanceSheetBalancer:
    """
    Balances the balance sheet by solving for plug items.
    Standard plugs: Cash (asset), Revolver/Debt (liability), Retained Earnings (equity).
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

    def balance(
        self,
        bs_data: pd.DataFrame,
        cf_data: pd.DataFrame,
        is_data: pd.DataFrame,
        periods: PeriodIndex,
        assumptions: Assumptions,
        balance_sheet_stmt: "Statement" = None,
    ) -> BalanceResult:
        """
        Balance BS: Assets = Liabilities + Equity
        Single-pass solve for plugs (cash, revolver). RE is calculated from NI.
        """
        errors = []
        plug_values = {}

        for period_idx in range(len(periods)):
            # First, set retained earnings from NI (RE is calculated, not a plug)
            if self.retained_earnings_line in bs_data.index:
                ni = is_data.loc["Net Income"].iloc[period_idx] if "Net Income" in is_data.index else 0
                dividends = is_data.loc["Dividends"].iloc[period_idx] if "Dividends" in is_data.index else 0
                prev_re = bs_data.loc[self.retained_earnings_line].iloc[period_idx - 1] if period_idx > 0 else 0
                bs_data.loc[self.retained_earnings_line, bs_data.columns[period_idx]] = prev_re + ni - dividends

            # Calculate totals EXCLUDING only cash and revolver plugs
            # Use _sum_* methods (explicit leaf items) to avoid double-counting aggregates like PP&E Gross + Net
            assets_excl = self._sum_assets(bs_data, period_idx, exclude_plugs=True)
            liabilities_excl = self._sum_liabilities(bs_data, period_idx, exclude_plugs=True)
            equity_excl = self._sum_equity(bs_data, period_idx, exclude_plugs=True)

            # Imbalance: Assets_excl - (Liab_excl + Equity_excl)
            diff = assets_excl - (liabilities_excl + equity_excl)

            # Solve for plugs (cash, revolver):
            # If diff > 0: Assets_excl > L+E_excl, need to reduce assets or increase L+E
            #   -> decrease cash (asset) or increase revolver (liability)
            # If diff < 0: Assets_excl < L+E_excl, need to increase assets or reduce L+E
            #   -> increase cash (asset) or decrease revolver (liability)
            cash_val = bs_data.loc[self.cash_line].iloc[period_idx] if self.cash_line in bs_data.index else 0
            if pd.isna(cash_val):
                cash_val = 0
                bs_data.loc[self.cash_line, bs_data.columns[period_idx]] = 0
            revolver_val = bs_data.loc[self.revolver_line].iloc[period_idx] if self.revolver_line in bs_data.index else 0
            if pd.isna(revolver_val):
                revolver_val = 0
                if self.revolver_line in bs_data.index:
                    bs_data.loc[self.revolver_line, bs_data.columns[period_idx]] = 0

            if diff > 0:  # Assets > L+E
                # Priority: pay down revolver first, then reduce cash
                if self.revolver_line in bs_data.index and revolver_val > 0:
                    paydown = min(revolver_val, diff)
                    new_revolver = revolver_val - paydown
                    bs_data.loc[self.revolver_line, bs_data.columns[period_idx]] = max(0, new_revolver)
                    diff -= paydown
                    plug_values[f"{self.revolver_line}_{period_idx}"] = new_revolver
                if diff > 0 and self.cash_line in bs_data.index:
                    new_cash = cash_val - diff
                    bs_data.loc[self.cash_line, bs_data.columns[period_idx]] = max(0, new_cash)
                    plug_values[f"{self.cash_line}_{period_idx}"] = max(0, new_cash)
            else:  # Assets < L+E
                # Priority: increase cash first, then draw revolver
                if self.cash_line in bs_data.index:
                    new_cash = cash_val - diff  # diff is negative
                    bs_data.loc[self.cash_line, bs_data.columns[period_idx]] = new_cash
                    plug_values[f"{self.cash_line}_{period_idx}"] = new_cash
                elif self.revolver_line in bs_data.index:
                    new_revolver = revolver_val - diff  # diff negative, increases revolver
                    bs_data.loc[self.revolver_line, bs_data.columns[period_idx]] = new_revolver
                    plug_values[f"{self.revolver_line}_{period_idx}"] = new_revolver

        # Recalculate aggregate lines (Total Assets, Total Liabilities, Total Equity) after plug solving
        self._recalculate_aggregates(bs_data)

        # Final verification with ALL items (including plugs)
        max_diff = 0
        for period_idx in range(len(periods)):
            # Use explicit total lines if they exist (recalculated from leaf items)
            if "Total Assets" in bs_data.index:
                assets = bs_data.loc["Total Assets"].iloc[period_idx]
            else:
                assets = self._sum_assets(bs_data, period_idx, exclude_plugs=False)

            if "Total Liabilities" in bs_data.index and "Total Equity" in bs_data.index:
                le = (bs_data.loc["Total Liabilities"].iloc[period_idx] +
                      bs_data.loc["Total Equity"].iloc[period_idx])
            else:
                le = (self._sum_liabilities(bs_data, period_idx, exclude_plugs=False) +
                      self._sum_equity(bs_data, period_idx, exclude_plugs=False))
            max_diff = max(max_diff, abs(assets - le))

        return BalanceResult(
            balanced=max_diff < self.tolerance,
            iterations=1,
            max_diff=max_diff,
            plug_values=plug_values,
            errors=errors,
        )

    def _total_assets(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all asset line items (exclude totals/aggregates)."""
        # Skip aggregate/total lines
        skip_keywords = ["Total", "Aggregate", "Sum"]

        asset_keywords = ["Cash", "Receivable", "Inventory", "Prepaid", "Property", "Plant", "Equipment",
                          "Intangible", "Goodwill", "Investment"]
        liability_keywords = ["Payable", "Accrued", "Deferred", "Debt", "Liability", "Revolver", "Loan",
                              "Borrowing", "Obligation"]
        equity_keywords = ["Stock", "Capital", "Retained", "Earnings", "Equity", "Treasury", "APIC"]

        total = 0
        for line in bs_data.index:
            line_lower = line.lower()
            # Skip aggregate lines
            if any(kw.lower() in line_lower for kw in skip_keywords):
                continue
            # Skip plug lines if exclude_plugs
            if exclude_plugs and (line == self.cash_line or line == self.retained_earnings_line):
                continue
            # Classify as asset
            is_asset = any(kw.lower() in line_lower for kw in asset_keywords)
            is_liability = any(kw.lower() in line_lower for kw in liability_keywords)
            is_equity = any(kw.lower() in line_lower for kw in equity_keywords)

            if is_asset and not is_liability and not is_equity:
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total

    def _total_liabilities(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all liability line items (exclude totals/aggregates)."""
        skip_keywords = ["Total", "Aggregate", "Sum"]

        liability_keywords = ["Payable", "Accrued", "Deferred", "Debt", "Liability", "Revolver", "Loan",
                              "Borrowing", "Obligation"]
        equity_keywords = ["Stock", "Capital", "Retained", "Earnings", "Equity", "Treasury", "APIC"]
        asset_keywords = ["Cash", "Receivable", "Inventory", "Prepaid", "Property", "Plant", "Equipment",
                          "Intangible", "Goodwill"]

        total = 0
        for line in bs_data.index:
            if exclude_plugs and line == self.revolver_line:
                continue
            line_lower = line.lower()
            # Skip aggregate lines
            if any(kw.lower() in line_lower for kw in skip_keywords):
                continue
            is_liability = any(kw.lower() in line_lower for kw in liability_keywords)
            is_equity = any(kw.lower() in line_lower for kw in equity_keywords)
            is_asset = any(kw.lower() in line_lower for kw in asset_keywords)

            if is_liability and not is_equity and not is_asset:
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total

    def _total_equity(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all equity line items (exclude totals/aggregates). RE is calculated, not a plug."""
        skip_keywords = ["Total", "Aggregate", "Sum"]

        equity_keywords = ["Stock", "Capital", "Retained", "Earnings", "Equity", "Treasury", "APIC", "Paid-In"]
        liability_keywords = ["Payable", "Accrued", "Deferred", "Debt", "Liability", "Revolver", "Loan", "Borrowing"]

        total = 0
        for line in bs_data.index:
            line_lower = line.lower()
            # Skip aggregate lines
            if any(kw.lower() in line_lower for kw in skip_keywords):
                continue
            # Skip plug lines if exclude_plugs (revolver is liability, not equity)
            # RE is NOT a plug - it's calculated from NI
            if exclude_plugs and line == self.revolver_line:
                continue
            is_equity = any(kw.lower() in line_lower for kw in equity_keywords)
            is_liability = any(kw.lower() in line_lower for kw in liability_keywords)

            if is_equity and not is_liability:
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total

    def _sum_assets(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all asset line items using explicit known asset line names (leaf items only)."""
        asset_names = [
            "Cash & Cash Equivalents",
            "Accounts Receivable",
            "Inventory",
            "Prepaid Expenses",
            "Property Plant & Equipment, Net",
            "Intangible Assets",
            "Goodwill",
        ]
        total = 0
        for line in asset_names:
            if line in bs_data.index:
                if exclude_plugs and line == self.cash_line:
                    continue
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total

    def _sum_liabilities(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all liability line items using explicit known liability line names (leaf items only)."""
        liability_names = [
            "Accounts Payable",
            "Accrued Expenses",
            "Deferred Revenue",
            "Current Portion of Long-Term Debt",
            "Revolver",
            "Long-Term Debt",
            "Deferred Tax Liability",
        ]
        total = 0
        for line in liability_names:
            if line in bs_data.index:
                if exclude_plugs and line == self.revolver_line:
                    continue
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total

    def _sum_equity(self, bs_data: pd.DataFrame, period_idx: int, exclude_plugs: bool = False) -> float:
        """Sum all equity line items using explicit known equity line names."""
        equity_names = [
            "Common Stock",
            "Additional Paid-In Capital",
            "Retained Earnings",
        ]
        total = 0
        for line in equity_names:
            if line in bs_data.index:
                val = bs_data.loc[line].iloc[period_idx]
                if pd.notna(val):
                    total += val
        return total


    def _recalculate_aggregates(self, bs_data: pd.DataFrame) -> None:
        """Recalculate aggregate lines (Total Assets, Total Liabilities, Total Equity) from leaf items."""
        for period_idx in range(bs_data.shape[1]):
            # Total Assets = sum of all asset leaf items
            if "Total Assets" in bs_data.index:
                total = self._sum_assets(bs_data, period_idx, exclude_plugs=False)
                bs_data.loc["Total Assets", bs_data.columns[period_idx]] = total

            # Total Liabilities = sum of all liability leaf items
            if "Total Liabilities" in bs_data.index:
                total = self._sum_liabilities(bs_data, period_idx, exclude_plugs=False)
                bs_data.loc["Total Liabilities", bs_data.columns[period_idx]] = total

            # Total Equity = sum of all equity leaf items
            if "Total Equity" in bs_data.index:
                total = self._sum_equity(bs_data, period_idx, exclude_plugs=False)
                bs_data.loc["Total Equity", bs_data.columns[period_idx]] = total

            # Also recalculate intermediate aggregates if they exist
            # Total Current Assets
            if "Total Current Assets" in bs_data.index:
                total = 0
                for line in ["Cash & Cash Equivalents", "Accounts Receivable", "Inventory", "Prepaid Expenses"]:
                    if line in bs_data.index:
                        val = bs_data.loc[line].iloc[period_idx]
                        if pd.notna(val):
                            total += val
                bs_data.loc["Total Current Assets", bs_data.columns[period_idx]] = total

            # Total Current Liabilities
            if "Total Current Liabilities" in bs_data.index:
                total = 0
                for line in ["Accounts Payable", "Accrued Expenses", "Deferred Revenue", "Current Portion of Long-Term Debt"]:
                    if line in bs_data.index:
                        val = bs_data.loc[line].iloc[period_idx]
                        if pd.notna(val):
                            total += val
                bs_data.loc["Total Current Liabilities", bs_data.columns[period_idx]] = total

class CashFlowBalancer:
    """
    Balances cash flow statement: Net Change in Cash = BS Cash Change.
    Ensures CF ties to Balance Sheet cash movement.
    """

    def __init__(
        self,
        cash_line: str = "Cash & Cash Equivalents",
        net_change_line: str = "Net Change in Cash",
        beginning_cash_line: str = "Beginning Cash Balance",
    ):
        self.cash_line = cash_line
        self.net_change_line = net_change_line
        self.beginning_cash_line = beginning_cash_line

    def balance(
        self,
        bs_data: pd.DataFrame,
        cf_data: pd.DataFrame,
        periods: PeriodIndex,
    ) -> BalanceResult:
        errors = []
        plug_values = {}

        for period_idx in range(len(periods)):
            # Beginning cash
            if period_idx == 0:
                beginning_cash = bs_data.loc[self.cash_line].iloc[0] if self.cash_line in bs_data.index else 0
            else:
                beginning_cash = bs_data.loc[self.cash_line].iloc[period_idx - 1]

            # Ending cash from BS
            ending_cash = bs_data.loc[self.cash_line].iloc[period_idx] if self.cash_line in bs_data.index else 0

            # Required net change
            required_change = ending_cash - beginning_cash

            # Calculated net change from CF (before plug)
            calc_change = 0
            for line in cf_data.index:
                if line != self.net_change_line and line != self.beginning_cash_line:
                    calc_change += cf_data.loc[line].iloc[period_idx]

            # Plug the difference
            plug = required_change - calc_change
            if self.net_change_line in cf_data.index:
                cf_data.loc[self.net_change_line, cf_data.columns[period_idx]] = required_change
                plug_values[f"{self.net_change_line}_{period_idx}"] = plug

            # Set beginning cash
            if self.beginning_cash_line in cf_data.index:
                cf_data.loc[self.beginning_cash_line, cf_data.columns[period_idx]] = beginning_cash

        # Verify
        max_diff = 0
        for period_idx in range(len(periods)):
            beginning = cf_data.loc[self.beginning_cash_line].iloc[period_idx] if self.beginning_cash_line in cf_data.index else 0
            net_change = cf_data.loc[self.net_change_line].iloc[period_idx] if self.net_change_line in cf_data.index else 0
            implied_ending = beginning + net_change
            actual_ending = bs_data.loc[self.cash_line].iloc[period_idx] if self.cash_line in bs_data.index else 0
            max_diff = max(max_diff, abs(implied_ending - actual_ending))

        return BalanceResult(
            balanced=max_diff < 1e-6,
            iterations=1,
            max_diff=max_diff,
            plug_values=plug_values,
            errors=errors,
        )