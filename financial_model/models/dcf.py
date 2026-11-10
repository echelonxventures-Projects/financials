"""
DCF Valuation Model.
Extends three-statement with WACC, terminal value, sensitivity analysis.
"""

from dataclasses import dataclass, field
from typing import Any
import pandas as pd
import numpy as np

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule


class DCFModel(ThreeStatementModel):
    """
    DCF valuation model with:
    - WACC calculation (CAPM)
    - Terminal value (Gordon Growth & Exit Multiple)
    - Sensitivity tables
    - Enterprise/Equity value bridge
    """

    def __init__(
        self,
        name: str = "DCF Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
        risk_free_rate: float = 0.045,
        equity_risk_premium: float = 0.055,
        beta: float = 1.2,
        cost_of_debt: float = 0.06,
        tax_rate: float = 0.21,
        target_debt_ratio: float = 0.20,
        terminal_growth: float = 0.025,
        exit_multiple: float = 12.0,
        projection_years: int = 5,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self.risk_free_rate = risk_free_rate
        self.equity_risk_premium = equity_risk_premium
        self.beta = beta
        self.cost_of_debt = cost_of_debt
        self.tax_rate = tax_rate
        self.target_debt_ratio = target_debt_ratio
        self.terminal_growth = terminal_growth
        self.exit_multiple = exit_multiple
        self.projection_years = projection_years
        self._add_dcf_assumptions()

    def _add_dcf_assumptions(self) -> None:
        """Add DCF-specific drivers to assumptions."""
        dcf_drivers = {
            "risk_free_rate": Driver("risk_free_rate", self.risk_free_rate, "Risk-free rate", "%"),
            "equity_risk_premium": Driver("equity_risk_premium", self.equity_risk_premium, "Equity risk premium", "%"),
            "beta": Driver("beta", self.beta, "Levered beta", "x"),
            "cost_of_debt": Driver("cost_of_debt", self.cost_of_debt, "Pre-tax cost of debt", "%"),
            "tax_rate": Driver("tax_rate", self.tax_rate, "Tax rate", "%"),
            "target_debt_ratio": Driver("target_debt_ratio", self.target_debt_ratio, "Target D/(D+E)", "%"),
            "terminal_growth": Driver("terminal_growth", self.terminal_growth, "Terminal growth rate", "%"),
            "exit_multiple": Driver("exit_multiple", self.exit_multiple, "Exit EV/EBITDA multiple", "x"),
        }
        for name, driver in dcf_drivers.items():
            self.assumptions.set_driver(driver)

    def calculate_dcf(self, result: Any = None) -> dict[str, Any]:
        """Run DCF valuation on model result."""
        if result is None:
            result = self.calculate()

        is_data = result.income_statement
        bs_data = result.balance_sheet
        periods = result.periods

        # Get assumptions
        rf = self.assumptions.get_value("risk_free_rate", 0)
        erp = self.assumptions.get_value("equity_risk_premium", 0)
        beta = self.assumptions.get_value("beta", 0)
        cod = self.assumptions.get_value("cost_of_debt", 0)
        tax = self.assumptions.get_value("tax_rate", 0)
        debt_ratio = self.assumptions.get_value("target_debt_ratio", 0)
        tg = self.assumptions.get_value("terminal_growth", 0)
        exit_mult = self.assumptions.get_value("exit_multiple", 0)

        # WACC
        cost_of_equity = rf + beta * erp
        after_tax_cod = cod * (1 - tax)
        wacc = cost_of_equity * (1 - debt_ratio) + after_tax_cod * debt_ratio

        # Free Cash Flow projection
        fcf = []
        for i in range(len(periods)):
            ebit = is_data.loc["Operating Income"].iloc[i] if "Operating Income" in is_data.index else 0
            nopat = ebit * (1 - tax)
            dna = is_data.loc["Depreciation & Amortization"].iloc[i] if "Depreciation & Amortization" in is_data.index else 0
            capex = abs(bs_data.loc["Capital Expenditures"].iloc[i]) if "Capital Expenditures" in bs_data.index else 0
            # Simplified WC change
            wc_change = 0
            fcf_val = nopat + dna - capex - wc_change
            fcf.append(fcf_val)

        # Discount factors
        discount_factors = [1 / (1 + wacc) ** (i + 1) for i in range(len(periods))]
        pv_fcf = [fcf[i] * discount_factors[i] for i in range(len(periods))]

        # Terminal Value (Gordon Growth)
        terminal_fcf = fcf[-1] * (1 + tg)
        terminal_value_gordon = terminal_fcf / (wacc - tg) if wacc > tg else 0
        pv_terminal_gordon = terminal_value_gordon * discount_factors[-1]

        # Terminal Value (Exit Multiple)
        ebitda = is_data.loc["Operating Income"].iloc[-1] + (is_data.loc["Depreciation & Amortization"].iloc[-1]
                                                                 if "Depreciation & Amortization" in is_data.index else 0)
        terminal_value_multiple = ebitda * exit_mult
        pv_terminal_multiple = terminal_value_multiple * discount_factors[-1]

        # Enterprise Value
        ev_gordon = sum(pv_fcf) + pv_terminal_gordon
        ev_multiple = sum(pv_fcf) + pv_terminal_multiple

        # Net Debt
        cash = bs_data.loc["Cash & Cash Equivalents"].iloc[-1] if "Cash & Cash Equivalents" in bs_data.index else 0
        # Sum all debt items
        debt_items = ["Long-Term Debt", "Revolver", "Term Loan A", "Term Loan B", "Subordinated Debt", "Mezzanine Debt"]
        total_debt = sum(bs_data.loc[d].iloc[-1] for d in debt_items if d in bs_data.index and pd.notna(bs_data.loc[d].iloc[-1]))
        net_debt = total_debt - cash

        # Equity Value
        equity_gordon = ev_gordon - net_debt
        equity_multiple = ev_multiple - net_debt

        # Shares
        shares = 1_000_000  # Default
        price_gordon = equity_gordon / shares if shares > 0 else 0
        price_multiple = equity_multiple / shares if shares > 0 else 0

        return {
            "wacc": wacc,
            "cost_of_equity": cost_of_equity,
            "after_tax_cod": after_tax_cod,
            "fcf": fcf,
            "discount_factors": discount_factors,
            "pv_fcf": pv_fcf,
            "terminal_value_gordon": terminal_value_gordon,
            "pv_terminal_gordon": pv_terminal_gordon,
            "terminal_value_multiple": terminal_value_multiple,
            "pv_terminal_multiple": pv_terminal_multiple,
            "ev_gordon": ev_gordon,
            "ev_multiple": ev_multiple,
            "net_debt": net_debt,
            "equity_gordon": equity_gordon,
            "equity_multiple": equity_multiple,
            "price_gordon": price_gordon,
            "price_multiple": price_multiple,
            "assumptions": {
                "risk_free_rate": rf,
                "equity_risk_premium": erp,
                "beta": beta,
                "cost_of_debt": cod,
                "tax_rate": tax,
                "target_debt_ratio": debt_ratio,
                "terminal_growth": tg,
                "exit_multiple": exit_mult,
            }
        }

    def sensitivity_analysis(
        self,
        result: Any = None,
        wacc_range: tuple[float, float] = (0.07, 0.13),
        growth_range: tuple[float, float] = (0.01, 0.04),
        steps: int = 5,
    ) -> pd.DataFrame:
        """Generate sensitivity table: equity value vs WACC and terminal growth."""
        if result is None:
            result = self.calculate()

        base_dcf = self.calculate_dcf(result)
        base_wacc = base_dcf["wacc"]
        base_growth = self.assumptions.get_value("terminal_growth", 0)

        wacc_vals = np.linspace(wacc_range[0], wacc_range[1], steps)
        growth_vals = np.linspace(growth_range[0], growth_range[1], steps)

        table = pd.DataFrame(index=[f"{g:.1%}" for g in growth_vals], columns=[f"{w:.1%}" for w in wacc_vals])

        for i, g in enumerate(growth_vals):
            for j, w in enumerate(wacc_vals):
                # Quick approximation using Gordon growth
                fcf = base_dcf["fcf"]
                pv_fcf = sum(fcf[k] / (1 + w) ** (k + 1) for k in range(len(fcf)))
                tv = fcf[-1] * (1 + g) / (w - g) if w > g else 0
                pv_tv = tv / (1 + w) ** len(fcf)
                ev = pv_fcf + pv_tv
                equity = ev - base_dcf["net_debt"]
                table.iloc[i, j] = equity

        return table