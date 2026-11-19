"""
DCF Valuation Model.
Extends the three-statement model with WACC (CAPM), terminal value
(Gordon growth and exit multiple), an EV-to-equity bridge and sensitivity tables.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.line_item import Driver
from financial_model.core.model import ModelResult
from financial_model.core.period import PeriodIndex
from financial_model.models.three_statement import ThreeStatementModel

WORKING_CAPITAL_LINES = [
    "Change in Accounts Receivable",
    "Change in Inventory",
    "Change in Prepaid Expenses",
    "Change in Accounts Payable",
    "Change in Accrued Expenses",
    "Change in Deferred Revenue",
    "Change in Deferred Taxes",
]
DEBT_LINES = [
    "Long-Term Debt",
    "Current Portion of Long-Term Debt",
    "Revolver",
    "Term Loan A",
    "Term Loan B",
    "Subordinated Debt",
    "Mezzanine Debt",
]


def _row(df: pd.DataFrame, line: str, idx: int, default: float = 0.0) -> float:
    if line not in df.index:
        return default
    val = df.loc[line].iloc[idx]
    return default if pd.isna(val) else float(val)


class DCFModel(ThreeStatementModel):
    """
    DCF valuation model with:
    - WACC calculation (CAPM)
    - Unlevered free cash flow (NOPAT + D&A - capex - change in NWC)
    - Terminal value (Gordon Growth & Exit Multiple)
    - Enterprise/Equity value bridge and sensitivity tables
    """

    model_type = "dcf"

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
        dcf_drivers = [
            Driver("risk_free_rate", self.risk_free_rate, "Risk-free rate", "%"),
            Driver("equity_risk_premium", self.equity_risk_premium, "Equity risk premium", "%"),
            Driver("beta", self.beta, "Levered beta", "x"),
            Driver("cost_of_debt", self.cost_of_debt, "Pre-tax cost of debt", "%"),
            Driver("tax_rate", self.tax_rate, "Tax rate", "%"),
            Driver("target_debt_ratio", self.target_debt_ratio, "Target D/(D+E)", "%"),
            Driver("terminal_growth", self.terminal_growth, "Terminal growth rate", "%"),
            Driver("exit_multiple", self.exit_multiple, "Exit EV/EBITDA multiple", "x"),
        ]
        for driver in dcf_drivers:
            self.assumptions.set_driver(driver)

    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #
    def wacc(self, drivers: dict[str, Driver] | None = None) -> dict[str, float]:
        drivers = drivers or self.assumptions.effective_drivers()

        def dv(name: str) -> float:
            d = drivers.get(name)
            return d.get_value(0) if d else 0.0

        cost_of_equity = dv("risk_free_rate") + dv("beta") * dv("equity_risk_premium")
        after_tax_cod = dv("cost_of_debt") * (1 - dv("tax_rate"))
        debt_ratio = dv("target_debt_ratio")
        return {
            "cost_of_equity": cost_of_equity,
            "after_tax_cod": after_tax_cod,
            "wacc": cost_of_equity * (1 - debt_ratio) + after_tax_cod * debt_ratio,
        }

    def free_cash_flows(self, result: ModelResult, tax_rate: float) -> list[dict[str, float]]:
        """Unlevered FCF per period: EBIT*(1-t) + D&A - capex + change in NWC."""
        is_data, bs_data, cf_data = result.income_statement, result.balance_sheet, result.cash_flow
        drivers = result.drivers
        rows = []
        for i in range(len(result.periods)):
            ebit = _row(is_data, "Operating Income", i)
            dna = abs(_row(is_data, "Depreciation & Amortization", i))
            if dna == 0:
                dna = abs(_row(cf_data, "Depreciation & Amortization", i))
            if "Capital Expenditures" in cf_data.index:
                capex = abs(_row(cf_data, "Capital Expenditures", i))
            elif "Capital Expenditures" in bs_data.index:
                capex = abs(_row(bs_data, "Capital Expenditures", i))
            else:
                capex = abs(drivers["capex"].get_value(i)) if "capex" in drivers else 0.0
            nwc_change = sum(_row(cf_data, line, i) for line in WORKING_CAPITAL_LINES)
            nopat = ebit * (1 - tax_rate)
            rows.append(
                {
                    "ebit": ebit,
                    "nopat": nopat,
                    "dna": dna,
                    "capex": capex,
                    "nwc_change": nwc_change,
                    "fcf": nopat + dna - capex + nwc_change,
                }
            )
        return rows

    def calculate_dcf(self, result: ModelResult | None = None) -> dict[str, Any]:
        """Run DCF valuation on a model result (valuation date = start of the first period)."""
        if result is None:
            result = self.calculate()

        drivers = result.drivers or self.assumptions.effective_drivers()

        def dv(name: str, idx: int = 0) -> float:
            d = drivers.get(name)
            return d.get_value(idx) if d else 0.0

        tax = dv("tax_rate")
        tg = dv("terminal_growth")
        exit_mult = dv("exit_multiple")
        rates = self.wacc(drivers)
        wacc = rates["wacc"]

        fcf_rows = self.free_cash_flows(result, tax)
        fcf = [r["fcf"] for r in fcf_rows]
        n = len(fcf)
        discount_factors = [1 / (1 + wacc) ** (i + 1) for i in range(n)]
        pv_fcf = [fcf[i] * discount_factors[i] for i in range(n)]

        terminal_fcf = fcf[-1] * (1 + tg) if n else 0.0
        terminal_value_gordon = terminal_fcf / (wacc - tg) if wacc > tg else 0.0
        pv_terminal_gordon = terminal_value_gordon * (discount_factors[-1] if n else 0.0)

        terminal_ebitda = (fcf_rows[-1]["ebit"] + fcf_rows[-1]["dna"]) if n else 0.0
        terminal_value_multiple = terminal_ebitda * exit_mult
        pv_terminal_multiple = terminal_value_multiple * (discount_factors[-1] if n else 0.0)

        ev_gordon = sum(pv_fcf) + pv_terminal_gordon
        ev_multiple = sum(pv_fcf) + pv_terminal_multiple

        # Net debt at the valuation date (opening balances)
        bs_data = result.balance_sheet
        opening_cash = (
            dv("beginning_cash") if "beginning_cash" in drivers else _row(bs_data, "Cash & Cash Equivalents", 0)
        )
        opening_debt = 0.0
        for line in DEBT_LINES:
            if line in bs_data.index:
                opening_debt += self.opening_balance(line, drivers) if line in self.OPENING_LINES else 0.0
        net_debt = opening_debt - opening_cash

        equity_gordon = ev_gordon - net_debt
        equity_multiple = ev_multiple - net_debt

        shares = dv("shares_outstanding") or 1_000_000
        price_gordon = equity_gordon / shares if shares > 0 else 0.0
        price_multiple = equity_multiple / shares if shares > 0 else 0.0

        return {
            "wacc": wacc,
            "cost_of_equity": rates["cost_of_equity"],
            "after_tax_cod": rates["after_tax_cod"],
            "fcf": fcf,
            "fcf_detail": fcf_rows,
            "discount_factors": discount_factors,
            "pv_fcf": pv_fcf,
            "terminal_ebitda": terminal_ebitda,
            "terminal_value_gordon": terminal_value_gordon,
            "pv_terminal_gordon": pv_terminal_gordon,
            "terminal_value_multiple": terminal_value_multiple,
            "pv_terminal_multiple": pv_terminal_multiple,
            "ev_gordon": ev_gordon,
            "ev_multiple": ev_multiple,
            "net_debt": net_debt,
            "equity_gordon": equity_gordon,
            "equity_multiple": equity_multiple,
            "shares_outstanding": shares,
            "price_gordon": price_gordon,
            "price_multiple": price_multiple,
            "implied_terminal_multiple": (terminal_value_gordon / terminal_ebitda) if terminal_ebitda else 0.0,
            "assumptions": {
                "risk_free_rate": dv("risk_free_rate"),
                "equity_risk_premium": dv("equity_risk_premium"),
                "beta": dv("beta"),
                "cost_of_debt": dv("cost_of_debt"),
                "tax_rate": tax,
                "target_debt_ratio": dv("target_debt_ratio"),
                "terminal_growth": tg,
                "exit_multiple": exit_mult,
            },
            "periods": [p.label for p in result.periods],
        }

    def sensitivity_analysis(
        self,
        result: ModelResult | None = None,
        wacc_range: tuple[float, float] = (0.07, 0.13),
        growth_range: tuple[float, float] = (0.01, 0.04),
        steps: int = 5,
    ) -> pd.DataFrame:
        """Equity value (Gordon growth) for a grid of WACC (columns) x terminal growth (rows)."""
        if result is None:
            result = self.calculate()

        base_dcf = self.calculate_dcf(result)
        fcf = base_dcf["fcf"]
        wacc_vals = np.linspace(wacc_range[0], wacc_range[1], steps)
        growth_vals = np.linspace(growth_range[0], growth_range[1], steps)

        table = pd.DataFrame(
            index=[f"{g:.1%}" for g in growth_vals], columns=[f"{w:.1%}" for w in wacc_vals], dtype=float
        )
        for i, g in enumerate(growth_vals):
            for j, w in enumerate(wacc_vals):
                pv_fcf = sum(fcf[k] / (1 + w) ** (k + 1) for k in range(len(fcf)))
                tv = fcf[-1] * (1 + g) / (w - g) if (fcf and w > g) else 0.0
                pv_tv = tv / (1 + w) ** len(fcf) if fcf else 0.0
                table.iloc[i, j] = pv_fcf + pv_tv - base_dcf["net_debt"]
        table.index.name = "Terminal growth \\ WACC"
        return table

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    def analysis_tables(self, result: ModelResult) -> dict[str, Any]:
        """Valuation summary, discounted FCF build and the WACC x growth sensitivity grid."""
        dcf = self.calculate_dcf(result)
        summary = {
            k: dcf[k]
            for k in (
                "wacc",
                "cost_of_equity",
                "after_tax_cod",
                "terminal_ebitda",
                "terminal_value_gordon",
                "pv_terminal_gordon",
                "terminal_value_multiple",
                "pv_terminal_multiple",
                "ev_gordon",
                "ev_multiple",
                "net_debt",
                "equity_gordon",
                "equity_multiple",
                "shares_outstanding",
                "price_gordon",
                "price_multiple",
                "implied_terminal_multiple",
            )
        }
        summary.update({f"assumption_{k}": v for k, v in dcf["assumptions"].items()})
        fcf = pd.DataFrame(dcf["fcf_detail"], index=dcf["periods"])
        fcf["discount_factor"] = dcf["discount_factors"]
        fcf["pv_fcf"] = dcf["pv_fcf"]
        fcf.index.name = "Period"
        return {
            "DCF Valuation": summary,
            "Free Cash Flow": fcf,
            "Sensitivity": self.sensitivity_analysis(result),
        }
