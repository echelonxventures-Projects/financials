"""
LBO Model.
Leveraged buyout with debt schedule, waterfall, IRR/MOIC.
"""

from dataclasses import dataclass
from typing import Any
import pandas as pd
import numpy as np

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule


class LBOModel(ThreeStatementModel):
    """
    LBO model with:
    - Sources & Uses
    - Debt tranches (Term Loan A/B, Revolver, Subordinated, Mezz)
    - Cash sweep (50/25/25 or custom)
    - IRR and MOIC calculation
    - Sponsor returns waterfall
    """

    def __init__(
        self,
        name: str = "LBO Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
        entry_multiple: float = 10.0,
        entry_ebitda: float = 50_000_000,
        equity_contribution: float = 0.40,
        tla_pct: float = 0.30,
        tla_rate: float = 0.065,
        tla_amort: float = 0.05,
        tlb_pct: float = 0.20,
        tlb_rate: float = 0.085,
        tlb_amort: float = 0.01,
        revolver_pct: float = 0.10,
        revolver_rate: float = 0.07,
        sub_pct: float = 0.10,
        sub_rate: float = 0.11,
        mezz_pct: float = 0.05,
        mezz_rate: float = 0.13,
        sweep_pct: float = 0.50,
        exit_multiple: float = 11.0,
        hold_years: int = 5,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self.entry_multiple = entry_multiple
        self.entry_ebitda = entry_ebitda
        self.equity_contribution = equity_contribution
        self.tla_pct = tla_pct
        self.tla_rate = tla_rate
        self.tla_amort = tla_amort
        self.tlb_pct = tlb_pct
        self.tlb_rate = tlb_rate
        self.tlb_amort = tlb_amort
        self.revolver_pct = revolver_pct
        self.revolver_rate = revolver_rate
        self.sub_pct = sub_pct
        self.sub_rate = sub_rate
        self.mezz_pct = mezz_pct
        self.mezz_rate = mezz_rate
        self.sweep_pct = sweep_pct
        self.exit_multiple = exit_multiple
        self.hold_years = hold_years
        self._add_lbo_assumptions()
        self._add_lbo_line_items()

    def _add_lbo_assumptions(self) -> None:
        lbo_drivers = {
            "entry_multiple": Driver("entry_multiple", self.entry_multiple, "Entry EV/EBITDA", "x"),
            "entry_ebitda": Driver("entry_ebitda", self.entry_ebitda, "LTM EBITDA at entry", "$"),
            "equity_contribution": Driver("equity_contribution", self.equity_contribution, "Equity % of purchase", "%"),
            "tla_pct": Driver("tla_pct", self.tla_pct, "Term Loan A %", "%"),
            "tla_rate": Driver("tla_rate", self.tla_rate, "TLA interest rate", "%"),
            "tla_amort": Driver("tla_amort", self.tla_amort, "TLA annual amortization", "%"),
            "tlb_pct": Driver("tlb_pct", self.tlb_pct, "Term Loan B %", "%"),
            "tlb_rate": Driver("tlb_rate", self.tlb_rate, "TLB interest rate", "%"),
            "tlb_amort": Driver("tlb_amort", self.tlb_amort, "TLB annual amortization", "%"),
            "revolver_pct": Driver("revolver_pct", self.revolver_pct, "Revolver %", "%"),
            "revolver_rate": Driver("revolver_rate", self.revolver_rate, "Revolver rate", "%"),
            "sub_pct": Driver("sub_pct", self.sub_pct, "Subordinated debt %", "%"),
            "sub_rate": Driver("sub_rate", self.sub_rate, "Sub debt rate", "%"),
            "mezz_pct": Driver("mezz_pct", self.mezz_pct, "Mezzanine %", "%"),
            "mezz_rate": Driver("mezz_rate", self.mezz_rate, "Mezz rate", "%"),
            "sweep_pct": Driver("sweep_pct", self.sweep_pct, "Cash sweep %", "%"),
            "exit_multiple": Driver("exit_multiple", self.exit_multiple, "Exit EV/EBITDA", "x"),
            "hold_years": Driver("hold_years", self.hold_years, "Hold period", "years"),
        }
        for name, driver in lbo_drivers.items():
            self.assumptions.set_driver(driver)

    def _add_lbo_line_items(self) -> None:
        """Add LBO-specific line items to statements."""
        # Compute initial debt amounts from sources/uses
        entry_ev = self.assumptions.get_value("entry_multiple", 0) * self.assumptions.get_value("entry_ebitda", 0)
        equity_pct = self.assumptions.get_value("equity_contribution", 0)
        debt_amt = entry_ev * (1 - equity_pct)

        tla_init = debt_amt * self.assumptions.get_value("tla_pct", 0)
        tlb_init = debt_amt * self.assumptions.get_value("tlb_pct", 0)
        sub_init = debt_amt * self.assumptions.get_value("sub_pct", 0)
        mezz_init = debt_amt * self.assumptions.get_value("mezz_pct", 0)

        import pandas as pd

        def _get_prev_balance(df, line_name, idx, default=0):
            """Helper to get previous period balance from MultiIndex or simple index."""
            if idx == 0:
                return default
            try:
                if isinstance(df.index, pd.MultiIndex):
                    for stmt in df.index.get_level_values(0).unique():
                        try:
                            val = df.loc[(stmt, line_name)].iloc[idx - 1]
                            if pd.notna(val):
                                return val
                        except KeyError:
                            continue
                else:
                    if line_name in df.index:
                        return df.loc[line_name].iloc[idx - 1]
            except (KeyError, IndexError):
                pass
            return default

        def _get_current_balance(df, line_name, idx, default=0):
            """Helper to get current period balance from MultiIndex or simple index."""
            try:
                if isinstance(df.index, pd.MultiIndex):
                    for stmt in df.index.get_level_values(0).unique():
                        try:
                            val = df.loc[(stmt, line_name)].iloc[idx]
                            if pd.notna(val):
                                return val
                        except KeyError:
                            continue
                else:
                    if line_name in df.index:
                        return df.loc[line_name].iloc[idx]
            except (KeyError, IndexError):
                pass
            return default

        # Add debt tranches to BS
        debt_items = [
            LineItem("Term Loan A", LineItemType.CALCULATED, "balance_sheet", order=165, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx, tla_init=tla_init: max(0, _get_prev_balance(df, "Term Loan A", idx)
                                                        - _get_current_balance(df, "TLA Principal Payment", idx)
                                                        - _get_current_balance(df, "TLA Sweep Payment", idx))
                                                    if _get_current_balance(df, "Term Loan A", idx) != 0 or idx > 0
                                                    else tla_init,
                     description="Term Loan A balance"),
            LineItem("Term Loan B", LineItemType.CALCULATED, "balance_sheet", order=166, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx, tlb_init=tlb_init: max(0, _get_prev_balance(df, "Term Loan B", idx)
                                                        - _get_current_balance(df, "TLB Principal Payment", idx)
                                                        - _get_current_balance(df, "TLB Sweep Payment", idx))
                                                    if _get_current_balance(df, "Term Loan B", idx) != 0 or idx > 0
                                                    else tlb_init,
                     description="Term Loan B balance"),
            LineItem("Subordinated Debt", LineItemType.CALCULATED, "balance_sheet", order=167, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx, sub_init=sub_init: _get_prev_balance(df, "Subordinated Debt", idx)
                                                    if _get_current_balance(df, "Subordinated Debt", idx) != 0 or idx > 0
                                                    else sub_init,
                     description="Sub debt balance"),
            LineItem("Mezzanine Debt", LineItemType.CALCULATED, "balance_sheet", order=168, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx, mezz_init=mezz_init: _get_prev_balance(df, "Mezzanine Debt", idx)
                                                    if _get_current_balance(df, "Mezzanine Debt", idx) != 0 or idx > 0
                                                    else mezz_init,
                     description="Mezzanine balance"),
        ]
        for item in debt_items:
            self.add_balance_sheet_item(item)

        # Add interest expense breakdown to IS
        interest_items = [
            LineItem("TLA Interest", LineItemType.CALCULATED, "income_statement", order=62, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Term Loan A"].iloc[idx-1] if idx > 0 else 0)
                                                    * df.assumptions.get_value("tla_rate", idx)
                                                    if hasattr(df, 'assumptions') else 0,
                     description="TLA interest"),
            LineItem("TLB Interest", LineItemType.CALCULATED, "income_statement", order=63, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Term Loan B"].iloc[idx-1] if idx > 0 else 0)
                                                    * df.assumptions.get_value("tlb_rate", idx)
                                                    if hasattr(df, 'assumptions') else 0,
                     description="TLB interest"),
            LineItem("Revolver Interest", LineItemType.CALCULATED, "income_statement", order=64, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Revolver"].iloc[idx-1] if idx > 0 else 0)
                                                    * df.assumptions.get_value("revolver_rate", idx)
                                                    if hasattr(df, 'assumptions') else 0,
                     description="Revolver interest"),
            LineItem("Sub Interest", LineItemType.CALCULATED, "income_statement", order=65, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Subordinated Debt"].iloc[idx-1] if idx > 0 else 0)
                                                    * df.assumptions.get_value("sub_rate", idx)
                                                    if hasattr(df, 'assumptions') else 0,
                     description="Sub debt interest"),
            LineItem("Mezz Interest", LineItemType.CALCULATED, "income_statement", order=66, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Mezzanine Debt"].iloc[idx-1] if idx > 0 else 0)
                                                    * df.assumptions.get_value("mezz_rate", idx)
                                                    if hasattr(df, 'assumptions') else 0,
                     description="Mezz interest"),
        ]
        for item in interest_items:
            self.add_income_statement_item(item)

    def sources_uses(self) -> pd.DataFrame:
        """Calculate Sources & Uses table."""
        entry_ev = self.assumptions.get_value("entry_multiple", 0) * self.assumptions.get_value("entry_ebitda", 0)
        equity_pct = self.assumptions.get_value("equity_contribution", 0)
        equity_amt = entry_ev * equity_pct
        debt_amt = entry_ev * (1 - equity_pct)

        tla = debt_amt * self.assumptions.get_value("tla_pct", 0)
        tlb = debt_amt * self.assumptions.get_value("tlb_pct", 0)
        revolver = debt_amt * self.assumptions.get_value("revolver_pct", 0)
        sub = debt_amt * self.assumptions.get_value("sub_pct", 0)
        mezz = debt_amt * self.assumptions.get_value("mezz_pct", 0)

        fees = entry_ev * 0.02  # 2% fees

        sources = pd.DataFrame({
            "Source": ["Term Loan A", "Term Loan B", "Revolver", "Subordinated Debt", "Mezzanine", "Sponsor Equity"],
            "Amount": [tla, tlb, revolver, sub, mezz, equity_amt],
            "% of Total": [tla/entry_ev, tlb/entry_ev, revolver/entry_ev, sub/entry_ev, mezz/entry_ev, equity_amt/entry_ev],
        })

        uses = pd.DataFrame({
            "Use": ["Enterprise Value", "Transaction Fees", "Refinancing Existing Debt"],
            "Amount": [entry_ev, fees, 0],
            "% of Total": [entry_ev/(entry_ev+fees), fees/(entry_ev+fees), 0],
        })

        return {"sources": sources, "uses": uses, "entry_ev": entry_ev, "equity_amt": equity_amt}

    def calculate_returns(self, result: Any = None) -> dict[str, Any]:
        """Calculate IRR, MOIC, and sponsor returns."""
        if result is None:
            result = self.calculate()

        bs_data = result.balance_sheet
        is_data = result.income_statement
        periods = result.periods

        su = self.sources_uses()
        equity_invested = su["equity_amt"]

        # Exit EBITDA (last projected year)
        exit_ebitda = is_data.loc["Operating Income"].iloc[-1]
        if "Depreciation & Amortization" in is_data.index:
            exit_ebitda += is_data.loc["Depreciation & Amortization"].iloc[-1]

        exit_mult = self.assumptions.get_value("exit_multiple", 0)
        exit_ev = exit_ebitda * exit_mult

        # Net debt at exit
        cash = bs_data.loc["Cash & Cash Equivalents"].iloc[-1] if "Cash & Cash Equivalents" in bs_data.index else 0
        tla = bs_data.loc["Term Loan A"].iloc[-1] if "Term Loan A" in bs_data.index else 0
        tlb = bs_data.loc["Term Loan B"].iloc[-1] if "Term Loan B" in bs_data.index else 0
        revolver = bs_data.loc["Revolver"].iloc[-1] if "Revolver" in bs_data.index else 0
        sub = bs_data.loc["Subordinated Debt"].iloc[-1] if "Subordinated Debt" in bs_data.index else 0
        mezz = bs_data.loc["Mezzanine Debt"].iloc[-1] if "Mezzanine Debt" in bs_data.index else 0

        net_debt = tla + tlb + revolver + sub + mezz - cash
        equity_value = exit_ev - net_debt

        # MOIC
        moic = equity_value / equity_invested if equity_invested > 0 else 0

        # IRR (approximate)
        hold_years = int(self.assumptions.get_value("hold_years", 0))
        irr = moic ** (1 / hold_years) - 1 if hold_years > 0 and moic > 0 else 0

        return {
            "entry_ev": su["entry_ev"],
            "equity_invested": equity_invested,
            "exit_ebitda": exit_ebitda,
            "exit_multiple": exit_mult,
            "exit_ev": exit_ev,
            "net_debt_at_exit": net_debt,
            "equity_value_at_exit": equity_value,
            "moic": moic,
            "irr": irr,
            "hold_years": hold_years,
        }