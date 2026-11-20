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

from typing import Any, ClassVar

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
    #: Business-specific drivers layered over the three-statement defaults.
    BUSINESS_DRIVERS: ClassVar[list[Driver]] = []
    #: Three-statement defaults this business model changes (e.g. AR days for consumers).
    DEFAULT_OVERRIDES: ClassVar[dict[str, Any]] = {}

    def __init__(self, name: str | None = None, periods: Any = None, assumptions: Any = None):
        super().__init__(name=name or type(self).__name__, periods=periods, assumptions=assumptions)
        self._engine_cache: tuple[dict[str, Driver], int, Engine] | None = None
        for driver in self.BUSINESS_DRIVERS:
            self.assumptions.set_driver(Driver(driver.name, driver.value, driver.description, driver.unit))
        for name_, value in self.DEFAULT_OVERRIDES.items():
            existing = self.assumptions.drivers.get(name_)
            desc, unit = (existing.description, existing.unit) if existing else ("", "")
            self.assumptions.set_driver(Driver(name_, value, desc, unit))
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
        stmt.remove_line_item("Revenue Growth")

        def revenue(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            return self.engine(drivers)["revenue"][idx]

        def cogs(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            return -abs(self.engine(drivers)["cogs"][idx])

        def sales_marketing(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            eng = self.engine(drivers)
            other = abs(_dv(drivers, "sm_pct", idx)) * eng["revenue"][idx]
            return -(abs(eng["acquisition_spend"][idx]) + other)

        replacements = {
            "Revenue": (revenue, "Revenue from the customer engine"),
            "COGS": (cogs, "Cost of revenue from the customer engine"),
            "Sales & Marketing Expense": (
                sales_marketing,
                "Customer acquisition spend + other S&M (sm_pct of revenue)",
            ),
        }
        for line, (formula, description) in replacements.items():
            item = stmt.get_line_item(line)
            assert item is not None
            stmt.add_line_item(
                LineItem(
                    line,
                    LineItemType.CALCULATED,
                    IS,
                    order=item.order,
                    unit="$",
                    calculation_rule=CalculationRule.CUSTOM,
                    custom_formula=formula,
                    is_total=item.is_total,
                    description=description,
                )
            )

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    def unit_economics(self, result: ModelResult) -> pd.DataFrame:
        """KPI table: one row per metric, one column per period."""
        eng = self.engine(result.drivers)
        is_df = result.income_statement
        revenue = is_df.loc["Revenue"].to_numpy(dtype=float)
        gross = is_df.loc["Gross Profit"].to_numpy(dtype=float)
        op_income = is_df.loc["Operating Income"].to_numpy(dtype=float)
        sm = -is_df.loc["Sales & Marketing Expense"].to_numpy(dtype=float)

        rows: dict[str, list[float | None]] = {}
        for key, label in self.KPI_ROWS:
            rows[label] = list(eng[key])

        ppy = self.periods_per_year
        gm = [_safe_div(g, r) for g, r in zip(gross, revenue)]
        rows["Gross Margin"] = gm
        rows["Operating Margin"] = [_safe_div(o, r) for o, r in zip(op_income, revenue)]
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
        rows["Revenue Growth (annualised)"] = growth
        rows["Rule of 40"] = [
            (g + m) if g is not None and m is not None else None for g, m in zip(growth, rows["Operating Margin"])
        ]
        new = eng["new_customers"]
        cac = [_safe_div(s, c) for s, c in zip(sm, new)]
        rows["Blended CAC (total S&M / new customers)"] = cac
        rows.update(self.lifetime_value_rows(eng, gm, cac))

        table = pd.DataFrame(rows, index=result.labels).T
        table.index.name = "Metric"
        return table

    #: (engine key, row label) pairs shown first in the Unit Economics table.
    KPI_ROWS: ClassVar[list[tuple[str, str]]] = []

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
            "Revenue per Customer per Month": arpa,
            "Lifetime Value (gross-margin basis)": ltv,
            "LTV / CAC": ratio,
            "CAC Payback (months)": payback,
        }

    def analysis_tables(self, result: ModelResult) -> dict[str, Any]:
        return {"Unit Economics": self.unit_economics(result)}


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
    BUSINESS_DRIVERS: ClassVar[list[Driver]] = [
        Driver("starting_customers", [200], "Customers at the start of the first period", "#"),
        Driver("starting_acv", [50_000], "Average ACV of the starting customer base", "$/yr"),
        Driver("sales_reps", [10, 14, 18, 22, 26], "Ramped quota-carrying reps (average in period)", "#"),
        Driver("quota_per_rep", [700_000], "Annual new-ARR quota per ramped rep", "$/yr"),
        Driver("quota_attainment", [0.70], "Average quota attainment", "%"),
        Driver("new_acv", [55_000], "ACV of newly won customers", "$/yr"),
        Driver("rep_cost", [250_000], "Fully loaded annual cost per rep (OTE, SE and SDR support)", "$/yr"),
        Driver("marketing_spend", [1_500_000], "Demand-generation spend per period", "$"),
        Driver("annual_logo_churn", [0.10], "Annual logo (and ARR) churn", "%"),
        Driver("annual_expansion", [0.18], "Annual expansion on existing ARR (upsell, seats, price)", "%"),
        Driver("services_pct", [0.05], "Professional services revenue as % of subscription revenue", "%"),
        Driver("services_cogs_pct", [0.80], "Cost of services as % of services revenue", "%"),
    ]
    DEFAULT_OVERRIDES: ClassVar[dict[str, Any]] = {
        "cogs_pct": [0.20],  # hosting + support on subscription revenue
        "sm_pct": [0.05],  # S&M beyond reps and demand gen (enablement, events, tools)
        "deferred_rev_pct": [0.40],  # annual upfront billing
        "ar_days": [60],
    }
    KPI_ROWS: ClassVar[list[tuple[str, str]]] = [
        ("end_customers", "Customers (end of period)"),
        ("new_customers", "New Logos"),
        ("churned_customers", "Churned Logos"),
        ("begin_arr", "Beginning ARR"),
        ("new_arr", "New ARR"),
        ("expansion_arr", "Expansion ARR"),
        ("churned_arr", "Churned ARR"),
        ("end_arr", "Ending ARR"),
        ("net_revenue_retention", "Net Revenue Retention (annualised)"),
        ("gross_revenue_retention", "Gross Revenue Retention (annualised)"),
        ("subscription_revenue", "Subscription Revenue"),
        ("services_revenue", "Services Revenue"),
        ("magic_number", "Magic Number (net new ARR / S&M)"),
    ]

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
    BUSINESS_DRIVERS: ClassVar[list[Driver]] = [
        Driver("starting_subscribers", [100_000], "Paying subscribers at the start", "#"),
        Driver("marketing_spend", [4_000_000], "Paid acquisition spend per period", "$"),
        Driver("paid_cac", [40], "Cost to acquire one paying subscriber through paid channels", "$"),
        Driver("organic_ratio", [0.60], "Organic / referral subscribers per paid subscriber", "x"),
        Driver("monthly_churn", [0.05], "Monthly subscriber churn", "%"),
        Driver("monthly_arpu", [9.99], "Average revenue per subscriber per month (net of discounts)", "$/mo"),
        Driver("payment_fee_pct", [0.15], "Payment processor / app-store fees as % of revenue", "%"),
    ]
    DEFAULT_OVERRIDES: ClassVar[dict[str, Any]] = {
        "cogs_pct": [0.10],  # content, hosting, support
        "sm_pct": [0.05],  # brand, community, team
        "ar_days": [5],  # consumers prepay; only processor settlement lag
        "deferred_rev_pct": [0.10],  # annual plans paid upfront
        "inventory_days": [0],
    }
    KPI_ROWS: ClassVar[list[tuple[str, str]]] = [
        ("end_customers", "Subscribers (end of period)"),
        ("paid_new", "New Subscribers - Paid"),
        ("organic_new", "New Subscribers - Organic"),
        ("new_customers", "New Subscribers - Total"),
        ("churned_customers", "Churned Subscribers"),
        ("net_adds", "Net Adds"),
        ("monthly_churn", "Monthly Churn"),
        ("arpu", "ARPU (monthly)"),
        ("end_arr", "Annualised Run-Rate Revenue (end)"),
        ("payment_fees", "Payment / App-Store Fees"),
    ]

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
            # Subscribers added during the period are exposed to churn for half of it on average.
            churned = min((subs + new / 2) * churn_p, subs + new)
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
    BUSINESS_DRIVERS: ClassVar[list[Driver]] = [
        Driver("starting_accounts", [1_000], "Paying accounts at the start", "#"),
        Driver("new_signups", [20_000], "Developer sign-ups per period", "#"),
        Driver("signup_conversion", [0.03], "Share of sign-ups that become paying accounts", "%"),
        Driver("annual_account_churn", [0.15], "Annual churn of paying accounts", "%"),
        Driver("usage_per_account", [5_000], "Starting monthly usage per paying account (units)", "units/mo"),
        Driver("usage_growth", [0.30], "Annual growth in usage per surviving account", "%"),
        Driver("free_units", [500], "Monthly free allowance per paying account (units, unbilled)", "units/mo"),
        Driver("price_per_unit", [0.10], "List price per billable unit", "$"),
        Driver("discount_pct", [0.05], "Average committed-use / volume discount", "%"),
        Driver("platform_fee", [100], "Monthly platform (seat / plan) fee per paying account", "$/mo"),
        Driver("free_accounts", [10_000], "Active free-tier accounts (average in period)", "#"),
        Driver("free_account_usage", [300], "Monthly usage per free-tier account (units)", "units/mo"),
        Driver("infra_cost_per_unit", [0.03], "Infrastructure cost per unit served", "$"),
        Driver("marketing_spend", [1_000_000], "Developer marketing / DevRel spend per period", "$"),
    ]
    DEFAULT_OVERRIDES: ClassVar[dict[str, Any]] = {
        "cogs_pct": [0.05],  # support, payment processing, third-party services
        "sm_pct": [0.10],  # sales team for larger accounts
        "ar_days": [30],
        "deferred_rev_pct": [0.05],  # mostly billed in arrears
    }
    KPI_ROWS: ClassVar[list[tuple[str, str]]] = [
        ("end_customers", "Paying Accounts (end of period)"),
        ("new_customers", "New Paying Accounts"),
        ("churned_customers", "Churned Accounts"),
        ("usage_per_account", "Usage per Account (units / month)"),
        ("billable_units", "Billable Units"),
        ("total_units", "Total Units Served (incl. free)"),
        ("usage_revenue", "Usage Revenue"),
        ("platform_revenue", "Platform Fee Revenue"),
        ("infra_cost", "Infrastructure Cost"),
        ("infra_margin", "Infrastructure Margin"),
        ("net_revenue_retention", "Net Revenue Retention (annualised)"),
        ("end_arr", "Annualised Run-Rate Revenue (end)"),
    ]

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


#: Scenario overlays shipped with each template (driver -> value).
TEMPLATE_SCENARIOS: dict[str, dict[str, dict[str, Any]]] = {
    "paas": {
        "base": {},
        "upside": {"signup_conversion": 0.04, "usage_growth": 0.40, "annual_account_churn": 0.10},
        "downside": {"signup_conversion": 0.02, "usage_growth": 0.15, "infra_cost_per_unit": 0.04},
    },
    "b2b-saas": {
        "base": {},
        "upside": {"quota_attainment": 0.80, "annual_expansion": 0.25, "annual_logo_churn": 0.07},
        "downside": {"quota_attainment": 0.55, "annual_expansion": 0.10, "annual_logo_churn": 0.15},
    },
    "b2c-subscription": {
        "base": {},
        "upside": {"paid_cac": 32, "monthly_churn": 0.04, "organic_ratio": 0.80},
        "downside": {"paid_cac": 55, "monthly_churn": 0.065, "organic_ratio": 0.40},
    },
}

TEMPLATE_NAMES = {
    "paas": "PaaS (usage-based platform) Model",
    "b2b-saas": "B2B SaaS (sales-led) Model",
    "b2c-subscription": "B2C Subscription Model",
}


def business_template(model_type: str, years: int = 5, start_year: int = 2025) -> dict[str, Any]:
    """Starter YAML config for a business-model type: its own drivers, the changed defaults, scenarios."""
    cls = BUSINESS_MODELS[model_type]
    drivers: dict[str, Any] = {}
    for d in cls.BUSINESS_DRIVERS:
        value = list(d.value) if isinstance(d.value, (list, tuple)) else d.value
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        elif isinstance(value, list):
            value = value[: max(1, years)]
        drivers[d.name] = {"value": value, "description": d.description, "unit": d.unit}
    defaults = ThreeStatementModel().assumptions.drivers
    for name, value in cls.DEFAULT_OVERRIDES.items():
        base = defaults.get(name)
        v = value[0] if isinstance(value, list) and len(value) == 1 else value
        drivers[name] = {"value": v, "description": base.description if base else "", "unit": base.unit if base else ""}
    drivers.update(
        {
            "rd_pct": {"value": 0.18, "description": "R&D as % of revenue", "unit": "%"},
            "gna_pct": {"value": 0.10, "description": "G&A as % of revenue", "unit": "%"},
            "beginning_cash": {"value": 5_000_000, "description": "Opening cash", "unit": "$"},
            "apic": {"value": 10_000_000, "description": "Opening paid-in capital", "unit": "$"},
        }
    )
    return {
        "name": TEMPLATE_NAMES[model_type],
        "model_type": model_type,
        "periods": {"start_year": start_year, "years": years, "type": "annual"},
        "assumptions": {
            "metadata": {"business_model": model_type, "currency": "USD"},
            "drivers": drivers,
            "scenarios": TEMPLATE_SCENARIOS[model_type],
        },
    }
