"""
Business-model templates: PaaS, B2B SaaS and B2C subscription companies.

Each class is a full :class:`ThreeStatementModel` (the statements tie, scenarios and
YAML overlays work the same way). The difference is where revenue, COGS and Sales &
Marketing come from: instead of "revenue grows x%" and "S&M is y% of revenue", a
customer engine rolls customers forward period by period from go-to-market drivers,
and the income statement reads its revenue and costs from that engine.

Driver units
------------
* **Flows** (``marketing_spend``, ``new_signups``) are amounts *per period*. With
  quarterly periods, enter quarterly spend.
* **Rates** are in their usual unit, whatever the period length: B2B and PaaS churn,
  expansion and usage growth are **annual**; B2C churn is **monthly**; ARPU, platform
  fees and per-account usage are **monthly**; ACV, quota and rep cost are **annual**.
  The engine converts them to the model's period length.
* **Stocks** (``starting_customers``, ``sales_reps``) are counts.

Every model adds a ``Unit Economics`` analysis table (customers, ARR, NRR/GRR, CAC,
LTV, LTV/CAC, payback, gross margin, rule of 40) that every exporter and the API
return next to the statements.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from financial_model.core.line_item import CalculationRule, Driver, LineItem, LineItemType
from financial_model.core.model import ModelResult
from financial_model.models.three_statement import IS, ThreeStatementModel

Engine = dict[str, list[float]]


def _dv(drivers: dict[str, Driver], name: str, idx: int, default: float = 0.0) -> float:
    driver = drivers.get(name)
    return driver.get_value(idx) if driver is not None else default


def per_period_rate(rate: float, periods_per_year: float, from_periods_per_year: float = 1.0) -> float:
    """Convert a loss rate (churn) quoted per ``from`` period to the model's period length."""
    rate = min(max(abs(rate), 0.0), 1.0)
    return 1.0 - (1.0 - rate) ** (from_periods_per_year / periods_per_year)


def per_period_growth(rate: float, periods_per_year: float) -> float:
    """Convert an annual growth rate to the model's period length."""
    return (1.0 + rate) ** (1.0 / periods_per_year) - 1.0


def _safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


class BusinessModel(ThreeStatementModel):
    """Three-statement model whose revenue, COGS and S&M come from a customer engine."""

    model_type = "business"

    def __init__(self, name: str | None = None, periods: Any = None, assumptions: Any = None):
        # Drivers, labels and KPI rows: models/definitions/<model_type>.yaml (extends business.yaml).
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self._engine_cache: tuple[dict[str, Driver], int, Engine] | None = None
        self.labels: dict[str, str] = self.setting("unit_economics_labels", {})
        self._wire_income_statement()

    # ------------------------------------------------------------------ #
    # Engine
    # ------------------------------------------------------------------ #
    @property
    def periods_per_year(self) -> int:
        return self.periods.period_type.periods_per_year

    def engine(self, drivers: dict[str, Driver]) -> Engine:
        """Customer roll-forward for every period (memoised per calculation)."""
        n = len(self.periods)
        cached = self._engine_cache
        if cached is not None and cached[0] is drivers and cached[1] == n:
            return cached[2]
        result = self.run_engine(drivers, n)
        # Holding a reference to ``drivers`` keeps its id from being reused by another dict.
        self._engine_cache = (drivers, n, result)
        return result

    def run_engine(self, drivers: dict[str, Driver], n: int) -> Engine:  # pragma: no cover - abstract
        raise NotImplementedError

    def acquisition_spend(self, drivers: dict[str, Driver], idx: int) -> float:
        """Go-to-market spend that buys new customers this period (positive magnitude)."""
        return self.engine(drivers)["acquisition_spend"][idx]

    # ------------------------------------------------------------------ #
    # Income statement wiring
    # ------------------------------------------------------------------ #
    def _wire_income_statement(self) -> None:
        stmt = self.income_statement
        for line in self.setting("engine_removes") or []:
            stmt.remove_line_item(str(line))

        def revenue(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            return self.engine(drivers)["revenue"][idx]

        def cogs(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            return -abs(self.engine(drivers)["cogs"][idx])

        def sales_marketing(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            eng = self.engine(drivers)
            other = abs(_dv(drivers, "sm_pct", idx)) * eng["revenue"][idx]
            return -(abs(eng["acquisition_spend"][idx]) + other)

        # Which lines the engine drives, and their descriptions: business.yaml `engine_lines` (role -> text).
        formulas = {"revenue": revenue, "cogs": cogs, "sales_marketing": sales_marketing}
        for role, description in (self.setting("engine_lines") or {}).items():
            if role not in formulas:
                raise ValueError(f"engine_lines: unknown role '{role}' (one of {', '.join(formulas)})")
            line, formula = self.line_name(role), formulas[role]
            item = stmt.get_line_item(line)
            if item is None:
                raise ValueError(f"the customer engine drives '{line}' ({role}), which is not in the income statement")
            stmt.add_line_item(
                LineItem(
                    line,
                    LineItemType.CALCULATED,
                    IS,
                    order=item.order,
                    unit=item.unit,
                    calculation_rule=CalculationRule.CUSTOM,
                    custom_formula=formula,
                    is_total=item.is_total,
                    description=str(description),
                )
            )

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    def unit_economics(self, result: ModelResult) -> pd.DataFrame:
        """KPI table: one row per metric, one column per period."""
        eng = self.engine(result.drivers)
        is_df = result.income_statement
        revenue = is_df.loc[self.line_name("revenue")].to_numpy(dtype=float)
        gross = is_df.loc[self.line_name("gross_profit")].to_numpy(dtype=float)
        op_income = is_df.loc[self.line_name("operating_income")].to_numpy(dtype=float)
        sm = -is_df.loc[self.line_name("sales_marketing")].to_numpy(dtype=float)

        rows: dict[str, list[float | None]] = {}
        for row in self.setting("kpi_rows", []):
            rows[str(row["label"])] = list(eng[row["key"]])

        ppy = self.periods_per_year
        gm = [_safe_div(g, r) for g, r in zip(gross, revenue)]
        rows[self.labels["gross_margin"]] = gm
        op_margin = [_safe_div(o, r) for o, r in zip(op_income, revenue)]
        rows[self.labels["operating_margin"]] = op_margin
        growth: list[float | None] = [None]
        for i in range(1, len(revenue)):
            # Year-over-year when there is a year of history, else period-over-period annualised.
            back = i - ppy
            if back >= 0 and revenue[back]:
                growth.append(revenue[i] / revenue[back] - 1)
            elif revenue[i - 1]:
                growth.append((revenue[i] / revenue[i - 1]) ** ppy - 1)
            else:
                growth.append(None)
        rows[self.labels["revenue_growth"]] = growth
        rows[self.labels["rule_of_40"]] = [
            (g + m) if g is not None and m is not None else None for g, m in zip(growth, op_margin)
        ]
        new = eng["new_customers"]
        cac = [_safe_div(s, c) for s, c in zip(sm, new)]
        rows[self.labels["blended_cac"]] = cac
        rows.update(self.lifetime_value_rows(eng, gm, cac))

        table = pd.DataFrame(rows, index=result.labels).T
        table.index.name = self.setting("analysis_index")
        return table

    def monthly_value_per_customer(self, eng: Engine, idx: int) -> float:
        """Revenue per average customer per month (ARPA / ARPU)."""
        avg = eng["average_customers"][idx]
        return eng["revenue"][idx] / avg / (12 / self.periods_per_year) if avg else 0.0

    def annual_churn(self, eng: Engine, idx: int) -> float:
        return eng["annual_logo_churn"][idx]

    def lifetime_value_rows(
        self, eng: Engine, gm: list[float | None], cac: list[float | None]
    ) -> dict[str, list[float | None]]:
        ltv: list[float | None] = []
        ratio: list[float | None] = []
        payback: list[float | None] = []
        arpa: list[float | None] = []
        for i, margin in enumerate(gm):
            monthly = self.monthly_value_per_customer(eng, i)
            arpa.append(monthly)
            churn = self.annual_churn(eng, i)
            monthly_gp = monthly * margin if margin is not None else None
            value = monthly_gp * 12 / churn if monthly_gp is not None and churn > 0 else None
            ltv.append(value)
            c = cac[i]
            ratio.append(_safe_div(value, c) if value is not None and c else None)
            payback.append(_safe_div(c, monthly_gp) if c is not None and monthly_gp else None)
        return {
            self.labels["revenue_per_customer"]: arpa,
            self.labels["lifetime_value"]: ltv,
            self.labels["ltv_cac"]: ratio,
            self.labels["cac_payback"]: payback,
        }

    def analysis_tables(self, result: ModelResult) -> dict[str, Any]:
        return {str(self.setting("analysis_table")): self.unit_economics(result)}


# ====================================================================== #
# B2B SaaS (sales-led)
# ====================================================================== #
class B2BSaaSModel(BusinessModel):
    """
    Sales-led B2B SaaS: quota-carrying reps book new ARR; existing ARR expands and churns.

    New ARR = ramped reps x annual quota x attainment (per period); new logos = new ARR / new ACV.
    Revenue = average ARR over the period (subscription) + professional services.
    S&M = reps x fully loaded rep cost + marketing spend + ``sm_pct`` x revenue.
    """

    model_type = "b2b-saas"

    def run_engine(self, drivers: dict[str, Driver], n: int) -> Engine:
        ppy = self.periods_per_year
        out: Engine = {}
        customers = abs(_dv(drivers, "starting_customers", 0))
        arr = customers * abs(_dv(drivers, "starting_acv", 0))
        for i in range(n):
            reps = abs(_dv(drivers, "sales_reps", i))
            new_arr = reps * abs(_dv(drivers, "quota_per_rep", i)) * abs(_dv(drivers, "quota_attainment", i)) / ppy
            acv = abs(_dv(drivers, "new_acv", i))
            new_logos = new_arr / acv if acv else 0.0
            churn_annual = min(abs(_dv(drivers, "annual_logo_churn", i)), 1.0)
            churn_p = per_period_rate(churn_annual, ppy)
            expansion_annual = _dv(drivers, "annual_expansion", i)
            churned_arr = arr * churn_p
            churned_logos = customers * churn_p
            expansion_arr = arr * per_period_growth(expansion_annual, ppy)
            end_arr = arr + new_arr + expansion_arr - churned_arr
            end_customers = customers + new_logos - churned_logos

            subscription = (arr + end_arr) / 2 / ppy
            services = subscription * abs(_dv(drivers, "services_pct", i))
            cogs = subscription * abs(_dv(drivers, "cogs_pct", i)) + services * abs(
                _dv(drivers, "services_cogs_pct", i)
            )
            spend = reps * abs(_dv(drivers, "rep_cost", i)) / ppy + abs(_dv(drivers, "marketing_spend", i))
            sm_total = spend + abs(_dv(drivers, "sm_pct", i)) * (subscription + services)

            values = {
                "begin_customers": customers,
                "new_customers": new_logos,
                "churned_customers": churned_logos,
                "end_customers": end_customers,
                "average_customers": (customers + end_customers) / 2,
                "begin_arr": arr,
                "new_arr": new_arr,
                "expansion_arr": expansion_arr,
                "churned_arr": churned_arr,
                "end_arr": end_arr,
                "net_revenue_retention": ((1 - churn_annual) * (1 + expansion_annual)),
                "gross_revenue_retention": 1 - churn_annual,
                "subscription_revenue": subscription,
                "services_revenue": services,
                "revenue": subscription + services,
                "cogs": cogs,
                "acquisition_spend": spend,
                "magic_number": (end_arr - arr) / sm_total if sm_total else 0.0,
                "annual_logo_churn": churn_annual,
            }
            for key, value in values.items():
                out.setdefault(key, []).append(value)
            arr, customers = end_arr, end_customers
        return out

    def monthly_value_per_customer(self, eng: Engine, idx: int) -> float:
        # Use the ACV-based ARR per customer (more stable than period revenue for B2B).
        avg = eng["average_customers"][idx]
        return (eng["begin_arr"][idx] + eng["end_arr"][idx]) / 2 / avg / 12 if avg else 0.0


# ====================================================================== #
# B2C subscription (consumer)
# ====================================================================== #
class B2CSubscriptionModel(BusinessModel):
    """
    Consumer subscription app: paid marketing buys subscribers, organic adds more, a
    monthly churn rate takes them away.

    New subscribers = marketing spend / paid CAC x (1 + organic ratio).
    Revenue = average subscribers x monthly ARPU (per period).
    COGS = revenue x (payment / app-store fees + content & hosting).
    """

    model_type = "b2c-subscription"

    def run_engine(self, drivers: dict[str, Driver], n: int) -> Engine:
        ppy = self.periods_per_year
        out: Engine = {}
        subs = abs(_dv(drivers, "starting_subscribers", 0))
        for i in range(n):
            spend = abs(_dv(drivers, "marketing_spend", i))
            cac = abs(_dv(drivers, "paid_cac", i))
            paid = spend / cac if cac else 0.0
            organic = paid * abs(_dv(drivers, "organic_ratio", i))
            new = paid + organic
            monthly_churn = min(abs(_dv(drivers, "monthly_churn", i)), 1.0)
            churn_p = per_period_rate(monthly_churn, ppy, from_periods_per_year=12)
            # Subscribers added during the period are exposed to churn for part of it.
            exposure = _dv(drivers, "new_subscriber_churn_exposure", i, 0.0)
            churned = min((subs + new * exposure) * churn_p, subs + new)
            end = subs + new - churned
            avg = (subs + end) / 2
            arpu = abs(_dv(drivers, "monthly_arpu", i))
            revenue = avg * arpu * 12 / ppy
            fees = revenue * abs(_dv(drivers, "payment_fee_pct", i))
            values = {
                "begin_customers": subs,
                "paid_new": paid,
                "organic_new": organic,
                "new_customers": new,
                "churned_customers": churned,
                "end_customers": end,
                "average_customers": avg,
                "net_adds": end - subs,
                "monthly_churn": monthly_churn,
                "arpu": arpu,
                "end_arr": end * arpu * 12,
                "revenue": revenue,
                "payment_fees": fees,
                "cogs": fees + revenue * abs(_dv(drivers, "cogs_pct", i)),
                "acquisition_spend": spend,
                "annual_logo_churn": per_period_rate(monthly_churn, 1, from_periods_per_year=12),
            }
            for key, value in values.items():
                out.setdefault(key, []).append(value)
            subs = end
        return out

    def annual_churn(self, eng: Engine, idx: int) -> float:
        # LTV for consumers is conventionally ARPU x GM / monthly churn; x12 / annualised churn
        # would understate it, so express monthly churn as an annual run-rate.
        return eng["monthly_churn"][idx] * 12


# ====================================================================== #
# PaaS (usage-based platform)
# ====================================================================== #
class PaaSModel(BusinessModel):
    """
    Usage-based platform (PaaS / API / infrastructure): developers sign up, a share
    convert to paying accounts, and each account's usage grows over time.

    Revenue = average paying accounts x (platform fee + billable units x unit price x
    (1 - discount)), monthly figures scaled to the period. Free-tier accounts and the
    free allowance of paying accounts consume infrastructure but are not billed.
    COGS = all units served x infrastructure cost per unit + ``cogs_pct`` x revenue.
    """

    model_type = "paas"

    def run_engine(self, drivers: dict[str, Driver], n: int) -> Engine:
        ppy = self.periods_per_year
        months = 12 / ppy
        out: Engine = {}
        accounts = abs(_dv(drivers, "starting_accounts", 0))
        usage = abs(_dv(drivers, "usage_per_account", 0))
        for i in range(n):
            growth = _dv(drivers, "usage_growth", i)
            if i:
                usage *= 1 + per_period_growth(growth, ppy)
            new = abs(_dv(drivers, "new_signups", i)) * abs(_dv(drivers, "signup_conversion", i))
            churn_annual = min(abs(_dv(drivers, "annual_account_churn", i)), 1.0)
            churned = accounts * per_period_rate(churn_annual, ppy)
            end = accounts + new - churned
            avg = (accounts + end) / 2

            billable_per = max(usage - abs(_dv(drivers, "free_units", i)), 0.0)
            price = abs(_dv(drivers, "price_per_unit", i)) * (1 - min(abs(_dv(drivers, "discount_pct", i)), 1.0))
            usage_rev = avg * billable_per * price * months
            platform_rev = avg * abs(_dv(drivers, "platform_fee", i)) * months
            revenue = usage_rev + platform_rev
            free_units = abs(_dv(drivers, "free_accounts", i)) * abs(_dv(drivers, "free_account_usage", i)) * months
            total_units = avg * usage * months + free_units
            infra = total_units * abs(_dv(drivers, "infra_cost_per_unit", i))
            monthly_rev_per_end = billable_per * price + abs(_dv(drivers, "platform_fee", i))
            values = {
                "begin_customers": accounts,
                "new_customers": new,
                "churned_customers": churned,
                "end_customers": end,
                "average_customers": avg,
                "usage_per_account": usage,
                "billable_units": avg * billable_per * months,
                "total_units": total_units,
                "usage_revenue": usage_rev,
                "platform_revenue": platform_rev,
                "revenue": revenue,
                "infra_cost": infra,
                "infra_margin": 1 - infra / revenue if revenue else 0.0,
                "cogs": infra + revenue * abs(_dv(drivers, "cogs_pct", i)),
                # Usage-based NRR: survivors keep paying and their usage compounds.
                "net_revenue_retention": (1 - churn_annual) * (1 + growth),
                "end_arr": end * monthly_rev_per_end * 12,
                "acquisition_spend": abs(_dv(drivers, "marketing_spend", i)),
                "annual_logo_churn": churn_annual,
            }
            for key, value in values.items():
                out.setdefault(key, []).append(value)
            accounts = end
        return out


BUSINESS_MODELS: dict[str, type[BusinessModel]] = {
    "paas": PaaSModel,
    "b2b-saas": B2BSaaSModel,
    "b2c-subscription": B2CSubscriptionModel,
}


def business_template(model_type: str, years: int = 5, start_year: int = 2025) -> dict[str, Any]:
    """Starter model config for a business-model type (see :func:`financial_model.core.starters.starter_config`)."""
    if model_type not in BUSINESS_MODELS:
        raise ValueError(f"'{model_type}' is not a business-model type")
    from financial_model.core.starters import starter_config

    return starter_config(model_type, years=years, start_year=start_year)
