"""
M&A Merger Model.
Accretion/dilution, synergy analysis, pro forma combination.
"""

from dataclasses import dataclass
from typing import Any
import pandas as pd
import numpy as np

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import LineItem, LineItemType


@dataclass
class MergerModel:
    """
    Merger model for accretion/dilution analysis.
    Combines acquirer and target into pro forma entity.
    """

    acquirer: ThreeStatementModel
    target: ThreeStatementModel

    # Deal terms
    offer_price_per_share: float = 0.0
    target_shares: float = 1_000_000
    cash_consideration_pct: float = 0.50
    stock_consideration_pct: float = 0.50
    synergies: float = 0.0  # Annual run-rate synergies
    synergy_phase_in: list[float] = None  # % realized each year
    financing_rate: float = 0.06
    tax_rate: float = 0.21

    def __post_init__(self):
        if self.synergy_phase_in is None:
            self.synergy_phase_in = [0.3, 0.6, 0.8, 1.0, 1.0]

    def calculate_accretion_dilution(self) -> dict[str, Any]:
        """Run accretion/dilution analysis."""
        acq_result = self.acquirer.calculate()
        tgt_result = self.target.calculate()

        acq_is = acq_result.income_statement
        tgt_is = tgt_result.income_statement
        acq_bs = acq_result.balance_sheet
        tgt_bs = tgt_result.balance_sheet

        periods = acq_result.periods
        n_periods = len(periods)

        # Acquirer standalone EPS
        acq_ni = acq_is.loc["Net Income"].iloc[0] if "Net Income" in acq_is.index else 0
        acq_shares = 10_000_000  # Would come from BS
        acq_eps = acq_ni / acq_shares if acq_shares > 0 else 0

        # Deal metrics
        equity_value = self.offer_price_per_share * self.target_shares
        cash_paid = equity_value * self.cash_consideration_pct
        stock_value = equity_value * self.stock_consideration_pct
        new_shares = stock_value / (acq_is.loc["Revenue"].iloc[0] / acq_shares * 10)  # Simplified: assume P/E

        # Pro forma Year 1
        pf_revenue = acq_is.loc["Revenue"].iloc[0] + tgt_is.loc["Revenue"].iloc[0]
        pf_ebit = (acq_is.loc["Operating Income"].iloc[0] if "Operating Income" in acq_is.index else 0) + \
                  (tgt_is.loc["Operating Income"].iloc[0] if "Operating Income" in tgt_is.index else 0)
        pf_synergies = self.synergies * self.synergy_phase_in[0]
        pf_ebit += pf_synergies

        # Interest on acquisition debt
        acq_debt = cash_paid
        interest_expense = acq_debt * self.financing_rate
        pf_ebit -= interest_expense

        pf_tax = pf_ebit * self.tax_rate if pf_ebit > 0 else 0
        pf_ni = pf_ebit - pf_tax

        pf_shares = acq_shares + new_shares
        pf_eps = pf_ni / pf_shares if pf_shares > 0 else 0

        accretion = (pf_eps - acq_eps) / acq_eps if acq_eps != 0 else 0

        return {
            "acquirer_ni": acq_ni,
            "acquirer_eps": acq_eps,
            "target_ni": tgt_is.loc["Net Income"].iloc[0] if "Net Income" in tgt_is.index else 0,
            "equity_value": equity_value,
            "cash_paid": cash_paid,
            "stock_value": stock_value,
            "new_shares_issued": new_shares,
            "pf_revenue": pf_revenue,
            "pf_ebit": pf_ebit,
            "pf_synergies": pf_synergies,
            "pf_interest": interest_expense,
            "pf_ni": pf_ni,
            "pf_shares": pf_shares,
            "pf_eps": pf_eps,
            "accretion_dilution_pct": accretion,
            "is_accretive": accretion > 0,
        }

    def contribution_analysis(self) -> pd.DataFrame:
        """Show each company's contribution to pro forma."""
        acq_result = self.acquirer.calculate()
        tgt_result = self.target.calculate()

        acq_is = acq_result.income_statement
        tgt_is = tgt_result.income_statement

        rows = []
        for label, acq_line, tgt_line in [
            ("Revenue", "Revenue", "Revenue"),
            ("EBITDA", "Operating Income", "Operating Income"),
            ("D&A", "Depreciation & Amortization", "Depreciation & Amortization"),
            ("EBIT", "Operating Income", "Operating Income"),
            ("Interest", "Interest Expense", "Interest Expense"),
            ("Pre-Tax", "Pre-Tax Income", "Pre-Tax Income"),
            ("Tax", "Income Tax Expense", "Income Tax Expense"),
            ("Net Income", "Net Income", "Net Income"),
        ]:
            acq_val = acq_is.loc[acq_line].iloc[0] if acq_line in acq_is.index else 0
            tgt_val = tgt_is.loc[tgt_line].iloc[0] if tgt_line in tgt_is.index else 0
            rows.append({
                "Metric": label,
                "Acquirer": acq_val,
                "Target": tgt_val,
                "Combined": acq_val + tgt_val,
                "Acquirer %": acq_val / (acq_val + tgt_val) if (acq_val + tgt_val) != 0 else 0,
            })

        return pd.DataFrame(rows)