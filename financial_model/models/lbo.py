"""
LBO Model.

Transaction mechanics
---------------------
* Purchase price (enterprise value) = ``entry_multiple`` x ``entry_ebitda``. When
  ``entry_ebitda`` is 0 the model's own first-year EBITDA is used.
* Uses = enterprise value + transaction fees (``fees_pct``) + refinancing of the
  target's existing term debt. Sources = new debt + sponsor equity, split by
  ``equity_contribution``. Funded debt is allocated across Term Loan A / B,
  subordinated and mezzanine notes in proportion to their ``*_pct`` drivers; the
  revolver is an undrawn commitment (``revolver_pct`` of funded debt) that the
  balance sheet plug draws on only if cash would fall below ``min_cash``.
* The opening balance sheet is re-cut at close: old debt and paid-in capital are
  removed, sponsor equity goes to APIC, retained earnings restart at zero and
  goodwill is the plug.
* Each year: interest accrues on opening balances, Term Loans A/B amortise
  (``tla_amort`` / ``tlb_amort`` of the original principal) and ``sweep_pct`` of
  cash above ``min_cash`` prepays Term Loan A then B.
* Exit after ``hold_years`` (capped at the projection length) at
  ``exit_multiple`` x EBITDA; equity = EV - net debt.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.definitions import load_definition
from financial_model.core.line_item import (
    CalculationRule,
    Driver,
    LineItem,
    LineItemType,
    lookup_value,
    previous_value,
)
from financial_model.core.model import ModelResult
from financial_model.core.period import PeriodIndex
from financial_model.models.three_statement import BS, CF, IS, ThreeStatementModel, _dv


def _tranche_tables(spec: list[dict[str, Any]]) -> tuple[dict[str, str], tuple[str, ...], dict[str, str]]:
    """Definition ``tranches`` -> (name -> driver key, amortising names, name -> interest line)."""
    tranches = {str(t["name"]): str(t["key"]) for t in spec}
    amortizing = tuple(str(t["name"]) for t in spec if t.get("amortizing"))
    interest = {str(t["name"]): str(t.get("interest_line") or f"{t['name']} Interest") for t in spec}
    return tranches, amortizing, interest


# The built-in tranche set (models/definitions/lbo.yaml). Instances read their own definition,
# so a definition override can add, remove or rename tranches.
TRANCHES, AMORTIZING, INTEREST_LINES = _tranche_tables(load_definition("lbo").get("tranches") or [])


def _row(df: pd.DataFrame, line: str, idx: int, default: float = 0.0) -> float:
    if line not in df.index:
        return default
    val = df.loc[line].iloc[idx]
    return default if pd.isna(val) else float(val)


class LBOModel(ThreeStatementModel):
    """
    LBO model with:
    - Sources & Uses
    - Debt tranches (Term Loan A/B, Revolver, Subordinated, Mezz)
    - Mandatory amortisation and cash sweep
    - IRR and MOIC
    """

    model_type = "lbo"

    def __init__(
        self,
        name: str | None = None,
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
        **overrides: Any,
    ):
        """Defaults come from models/definitions/lbo.yaml; keyword arguments override drivers by name."""
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self._ebitda0_cache: float | None = None
        self.tranches, self.amortizing, self.interest_lines = _tranche_tables(self.setting("tranches") or [])
        self._apply_overrides(overrides)
        self._add_lbo_line_items()

    # ------------------------------------------------------------------ #
    # Deal maths
    # ------------------------------------------------------------------ #
    def _driver_implied_ebitda(self, drivers: dict[str, Driver]) -> float:
        """First-year EBITDA implied by the standard driver set (used before any statements exist)."""
        revenue = _dv(drivers, "revenue", 0)
        margin = 1 - sum(abs(_dv(drivers, name, 0)) for name in ("cogs_pct", "rd_pct", "sm_pct", "gna_pct"))
        return revenue * margin - abs(_dv(drivers, "sbc", 0))

    def entry_ebitda_value(self, drivers: dict[str, Driver], df: pd.DataFrame | None = None) -> float:
        """EBITDA used for the entry valuation (driver, else first-year model EBITDA)."""
        explicit = _dv(drivers, "entry_ebitda", 0)
        if explicit > 0:
            return explicit
        if df is not None:
            ebit = lookup_value(df, "Operating Income", 0, statement=IS)
            dna = abs(lookup_value(df, "Depreciation & Amortization", 0, statement=IS))
            if ebit != 0 or dna != 0:
                self._ebitda0_cache = ebit + dna
        if self._ebitda0_cache is not None:
            return self._ebitda0_cache
        return self._driver_implied_ebitda(drivers)

    def deal(self, drivers: dict[str, Driver], df: pd.DataFrame | None = None) -> dict[str, Any]:
        """Sources & uses and opening tranche balances."""
        ebitda0 = self.entry_ebitda_value(drivers, df)
        if ebitda0 <= 0:
            raise ValueError(
                f"LBO entry EBITDA must be positive (got {ebitda0:,.0f}). Set the 'entry_ebitda' driver "
                "or use operating assumptions that are profitable in the first projected year."
            )
        entry_ev = _dv(drivers, "entry_multiple", 0) * ebitda0
        fees = entry_ev * abs(_dv(drivers, "fees_pct", 0))
        refinanced = _dv(drivers, "long_term_debt", 0) + _dv(drivers, "current_debt", 0)
        total_uses = entry_ev + fees + refinanced
        equity_pct = min(max(_dv(drivers, "equity_contribution", 0), 0.0), 1.0)
        equity = total_uses * equity_pct
        debt = total_uses - equity

        weights = {name: abs(_dv(drivers, f"{key}_pct", 0)) for name, key in self.tranches.items()}
        weight_sum = sum(weights.values())
        inits = {name: (debt * w / weight_sum if weight_sum > 0 else 0.0) for name, w in weights.items()}
        revolver_commitment = debt * abs(_dv(drivers, "revolver_pct", 0))

        opening_assets = (
            _dv(drivers, "beginning_cash", 0) + _dv(drivers, "ppe_gross", 0) + _dv(drivers, "intangibles", 0)
        )
        goodwill = total_uses + _dv(drivers, "dlt", 0) - opening_assets

        return {
            "ebitda0": ebitda0,
            "entry_ev": entry_ev,
            "fees": fees,
            "refinanced_debt": refinanced,
            "total_uses": total_uses,
            "equity": equity,
            "debt": debt,
            "tranches": inits,
            "revolver_commitment": revolver_commitment,
            "goodwill": goodwill,
        }

    # Opening balance sheet is re-cut at close -------------------------- #
    def opening_balance(self, line: str, drivers: dict[str, Driver], df: pd.DataFrame | None = None) -> float:
        if line in ("Long-Term Debt", "Current Portion of Long-Term Debt", "Common Stock"):
            return 0.0
        if line in self.tranches:
            return self.deal(drivers, df)["tranches"][line]
        if line == "Additional Paid-In Capital":
            return self.deal(drivers, df)["equity"]
        if line == "Goodwill":
            return self.deal(drivers, df)["goodwill"]
        return super().opening_balance(line, drivers)

    def opening_retained_earnings(self, drivers: dict[str, Driver]) -> float:  # noqa: ARG002
        return 0.0

    def _delta(self, df: pd.DataFrame, line: str, idx: int, drivers: dict[str, Driver], statement: str = BS) -> float:
        current = lookup_value(df, line, idx, statement=statement)
        prev = previous_value(df, line, idx, opening=self.opening_balance(line, drivers, df), statement=statement)
        return current - prev

    # ------------------------------------------------------------------ #
    # Debt schedule
    # ------------------------------------------------------------------ #
    def _opening_tranche(self, df: pd.DataFrame, name: str, idx: int, drivers: dict[str, Driver]) -> float:
        return previous_value(df, name, idx, opening=self.opening_balance(name, drivers, df), statement=BS)

    def debt_schedule_for_period(self, df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> dict[str, Any]:
        """Interest, mandatory amortisation, sweep and closing balance for every tranche in period ``idx``."""
        deal = self.deal(drivers, df)
        schedule: dict[str, Any] = {}
        mandatory_total = 0.0
        for name, key in self.tranches.items():
            opening = self._opening_tranche(df, name, idx, drivers)
            amort = 0.0
            if name in self.amortizing:
                amort = min(opening, deal["tranches"][name] * abs(_dv(drivers, f"{key}_amort", idx)))
            schedule[name] = {
                "opening": opening,
                "interest": opening * abs(_dv(drivers, f"{key}_rate", idx)),
                "amort": amort,
                "sweep": 0.0,
                "closing": opening - amort,
            }
            mandatory_total += amort

        # Cash available for the sweep: prior cash + flows before optional debt service
        prior_cash = previous_value(
            df, "Cash & Cash Equivalents", idx, opening=_dv(drivers, "beginning_cash", 0), statement=BS
        )
        ni = lookup_value(df, "Net Income", idx, statement=IS)
        dna = abs(lookup_value(df, "Depreciation & Amortization", idx, statement=IS))
        sbc = abs(lookup_value(df, "Stock-Based Compensation", idx, statement=IS))
        wc = (
            -self._delta(df, "Accounts Receivable", idx, drivers)
            - self._delta(df, "Inventory", idx, drivers)
            - self._delta(df, "Prepaid Expenses", idx, drivers)
            + self._delta(df, "Accounts Payable", idx, drivers)
            + self._delta(df, "Accrued Expenses", idx, drivers)
            + self._delta(df, "Deferred Revenue", idx, drivers)
            - (
                _dv(drivers, "intangibles", idx)
                - (_dv(drivers, "intangibles", idx - 1) if idx > 0 else _dv(drivers, "intangibles", 0))
            )
            + (_dv(drivers, "dlt", idx) - (_dv(drivers, "dlt", idx - 1) if idx > 0 else _dv(drivers, "dlt", 0)))
        )
        investing = -abs(_dv(drivers, "capex", idx)) - abs(_dv(drivers, "acquisitions", idx))
        other_financing = _dv(drivers, "equity_issuance", idx) - abs(_dv(drivers, "dividends", idx))
        cash_before_sweep = prior_cash + ni + dna + sbc + wc + investing + other_financing - mandatory_total
        excess = max(0.0, cash_before_sweep - _dv(drivers, "min_cash", idx))
        sweep_available = excess * min(max(_dv(drivers, "sweep_pct", idx), 0.0), 1.0)

        for name in self.amortizing:
            tranche = schedule[name]
            sweep = min(sweep_available, max(0.0, tranche["closing"]))
            tranche["sweep"] = sweep
            tranche["closing"] -= sweep
            sweep_available -= sweep

        schedule["_cash_before_sweep"] = cash_before_sweep
        schedule["_mandatory_total"] = mandatory_total
        schedule["_sweep_total"] = sum(schedule[n]["sweep"] for n in self.tranches)
        return schedule

    # ------------------------------------------------------------------ #
    # Line items
    # ------------------------------------------------------------------ #
    def _add_lbo_line_items(self) -> None:
        """Add the debt-schedule lines where the definition's ``layout`` puts them."""
        layout = self.setting("layout") or {}
        names = self.lbo_lines

        def tranche_balance(name: str) -> Any:
            return lambda df, idx, drivers: self.debt_schedule_for_period(df, idx, drivers)[name]["closing"]

        def tranche_interest(name: str, key: str) -> Any:
            return lambda df, idx, drivers: (
                -self._opening_tranche(df, name, idx, drivers) * abs(_dv(drivers, f"{key}_rate", idx))
            )

        def mandatory_amort(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            return -self.debt_schedule_for_period(df, idx, drivers)["_mandatory_total"]

        def sweep_repayment(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            # Residual of the balance-sheet movement so the cash flow ties exactly
            total_change = sum(self._delta(df, name, idx, drivers) for name in self.tranches)
            return total_change + self.debt_schedule_for_period(df, idx, drivers)["_mandatory_total"]

        def revolver_interest(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            return -previous_value(df, "Revolver", idx, opening=0.0, statement=BS) * abs(
                _dv(drivers, "revolver_rate", idx)
            )

        statements = {IS: self.income_statement, BS: self.balance_sheet, CF: self.cash_flow}

        def place(role: str, default_statement: str, name: str, order: float, **fields: Any) -> None:
            spec = layout.get(role) or {}
            statement = str(spec.get("statement", default_statement))
            description = str(spec.get("description", "")).replace("{name}", name)
            statements[statement].add_line_item(
                LineItem(
                    name,
                    fields.pop("line_type", LineItemType.CALCULATED),
                    statement,
                    order=order,
                    unit="$",
                    description=description,
                    **fields,
                )
            )
            for total in spec.get("add_to") or []:
                parent = statements[statement].get_line_item(total)
                if parent is not None and name not in parent.children:
                    parent.children.append(name)

        balance = layout.get("tranche_balance") or {}
        interest = layout.get("tranche_interest") or {}
        n = max(len(self.tranches), 1)
        for i, (name, key) in enumerate(self.tranches.items()):
            place(
                "tranche_balance",
                BS,
                name,
                float(balance.get("order", 0)) + i * float(balance.get("order_step", 1)),
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=tranche_balance(name),
            )
            place(
                "tranche_interest",
                IS,
                self.interest_lines[name],
                float(interest.get("order", 0)) + i / n,
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=tranche_interest(name, key),
            )

        def order(role: str) -> float:
            return float((layout.get(role) or {}).get("order", 0))

        place(
            "revolver_interest",
            IS,
            names["revolver_interest"],
            order("revolver_interest"),
            calculation_rule=CalculationRule.CUSTOM,
            custom_formula=revolver_interest,
        )
        # Total interest sums the tranche lines (replaces the input-driven line)
        place(
            "total_interest",
            IS,
            names["total_interest"],
            order("total_interest"),
            calculation_rule=CalculationRule.SUM_CHILDREN,
            children=[*self.interest_lines.values(), names["revolver_interest"]],
            is_total=True,
        )
        place(
            "mandatory_amortization",
            CF,
            names["mandatory_amortization"],
            order("mandatory_amortization"),
            calculation_rule=CalculationRule.CUSTOM,
            custom_formula=mandatory_amort,
        )
        place(
            "cash_sweep",
            CF,
            names["cash_sweep"],
            order("cash_sweep"),
            calculation_rule=CalculationRule.CUSTOM,
            custom_formula=sweep_repayment,
        )

    @property
    def lbo_lines(self) -> dict[str, str]:
        """Names of the single debt-schedule lines (definition ``layout.<role>.name``)."""
        layout = self.setting("layout") or {}
        return {
            role: str((layout.get(role) or {}).get("name") or role.replace("_", " ").title())
            for role in ("revolver_interest", "total_interest", "mandatory_amortization", "cash_sweep")
        }

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    def _drivers_for(self, result: ModelResult | None) -> dict[str, Driver]:
        if result is not None and result.drivers:
            return result.drivers
        return self.assumptions.effective_drivers()

    def sources_uses(self, result: ModelResult | None = None) -> dict[str, Any]:
        """Sources & Uses tables (runs the model first if entry EBITDA must be derived)."""
        drivers = self._drivers_for(result)
        if _dv(drivers, "entry_ebitda", 0) <= 0 and result is None and self._ebitda0_cache is None:
            result = self.calculate()
            drivers = result.drivers
        df = None
        if result is not None:
            df = self._combine(result.income_statement, result.balance_sheet, result.cash_flow)
        deal = self.deal(drivers, df)
        total = deal["total_uses"] or 1.0

        sources = pd.DataFrame(
            {
                "Source": [*self.tranches.keys(), "Revolver (undrawn)", "Sponsor Equity"],
                "Amount": [*(deal["tranches"][n] for n in self.tranches), 0.0, deal["equity"]],
                "Commitment": [
                    *(deal["tranches"][n] for n in self.tranches),
                    deal["revolver_commitment"],
                    deal["equity"],
                ],
            }
        )
        sources["% of Total"] = sources["Amount"] / total
        uses = pd.DataFrame(
            {
                "Use": ["Enterprise Value", "Transaction Fees", "Refinancing Existing Debt"],
                "Amount": [deal["entry_ev"], deal["fees"], deal["refinanced_debt"]],
            }
        )
        uses["% of Total"] = uses["Amount"] / total

        return {
            "sources": sources,
            "uses": uses,
            "entry_ev": deal["entry_ev"],
            "entry_ebitda": deal["ebitda0"],
            "fees": deal["fees"],
            "total_uses": deal["total_uses"],
            "equity_amt": deal["equity"],
            "debt_amt": deal["debt"],
            "goodwill": deal["goodwill"],
        }

    def debt_schedule(self, result: ModelResult | None = None) -> pd.DataFrame:
        """Per-period tranche balances, interest and repayments as a table."""
        result = result or self.calculate()
        bs, is_, cf = result.balance_sheet, result.income_statement, result.cash_flow
        lines = self.lbo_lines
        rows = []
        for i, period in enumerate(result.periods):
            row: dict[str, Any] = {"period": period.label}
            for name in self.tranches:
                row[f"{name}"] = _row(bs, name, i)
                row[f"{self.interest_lines[name]}"] = _row(is_, self.interest_lines[name], i)
            row["Revolver"] = _row(bs, "Revolver", i)
            for role in ("mandatory_amortization", "cash_sweep"):
                row[lines[role]] = _row(cf, lines[role], i)
            row["Total Debt"] = sum(_row(bs, n, i) for n in self.tranches) + _row(bs, "Revolver", i)
            row["Cash"] = _row(bs, "Cash & Cash Equivalents", i)
            rows.append(row)
        return pd.DataFrame(rows).set_index("period")

    def calculate_returns(self, result: ModelResult | None = None) -> dict[str, Any]:
        """Calculate sponsor IRR, MOIC and the exit bridge."""
        if result is None:
            result = self.calculate()

        bs_data = result.balance_sheet
        is_data = result.income_statement
        drivers = self._drivers_for(result)
        su = self.sources_uses(result)
        equity_invested = su["equity_amt"]

        n_periods = len(result.periods)
        hold_years = int(_dv(drivers, "hold_years", 0)) or n_periods
        exit_idx = max(0, min(hold_years, n_periods) - 1)
        years_held = exit_idx + 1

        exit_ebitda = _row(is_data, "Operating Income", exit_idx) + abs(
            _row(is_data, "Depreciation & Amortization", exit_idx)
        )
        exit_mult = _dv(drivers, "exit_multiple", exit_idx)
        exit_ev = exit_ebitda * exit_mult

        cash = _row(bs_data, "Cash & Cash Equivalents", exit_idx)
        debt = sum(_row(bs_data, n, exit_idx) for n in self.tranches) + _row(bs_data, "Revolver", exit_idx)
        debt += _row(bs_data, "Long-Term Debt", exit_idx) + _row(bs_data, "Current Portion of Long-Term Debt", exit_idx)
        net_debt = debt - cash
        equity_value = exit_ev - net_debt

        moic = equity_value / equity_invested if equity_invested > 0 else 0.0
        irr = moic ** (1 / years_held) - 1 if moic > 0 and years_held > 0 else -1.0 if equity_invested > 0 else 0.0

        return {
            "entry_ebitda": su["entry_ebitda"],
            "entry_ev": su["entry_ev"],
            "total_uses": su["total_uses"],
            "equity_invested": equity_invested,
            "debt_raised": su["debt_amt"],
            "exit_period": result.periods[exit_idx].label,
            "exit_ebitda": exit_ebitda,
            "exit_multiple": exit_mult,
            "exit_ev": exit_ev,
            "debt_at_exit": debt,
            "cash_at_exit": cash,
            "net_debt_at_exit": net_debt,
            "equity_value_at_exit": equity_value,
            "moic": moic,
            "irr": irr,
            "hold_years": years_held,
        }

    def analysis_tables(self, result: ModelResult) -> dict[str, Any]:
        """Sponsor returns, sources & uses and the tranche-level debt schedule."""
        su = self.sources_uses(result)
        return {
            "LBO Returns": self.calculate_returns(result),
            "Sources": su["sources"].set_index("Source"),
            "Uses": su["uses"].set_index("Use"),
            "Debt Schedule": self.debt_schedule(result),
        }
