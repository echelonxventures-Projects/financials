"""
Plan catalogue: what each commercial tier may do.

Plans are data, not code: the defaults below can be changed or extended from the
runtime settings file (``platform.plans``), so an operator sets their own prices,
quotas and tiers without editing the toolkit::

    platform:
      plans:
        pro: {price_monthly: 29, runs_per_month: 5000}
        startup: {name: Startup, audience: organization, price_monthly: 199, seats: 10}

``audience`` says who may hold the plan: ``individual`` (B2C self-serve accounts),
``organization`` (B2B teams) or ``any``. ``None`` for a limit means unlimited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

AUDIENCES = ("individual", "organization", "any")
EXPORT_FORMATS = ("json", "excel", "csv")
ALL = "*"


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    audience: str = "any"
    price_monthly: float | None = 0.0  # None = contact sales
    price_yearly: float | None = None
    per_seat: bool = False
    seats: int | None = 1
    runs_per_month: int | None = 100
    max_periods: int | None = 60
    model_types: tuple[str, ...] = (ALL,)
    exports: tuple[str, ...] = ("json",)
    charts: bool = False
    data_fetch: bool = False
    api_keys: int | None = 1
    support: str = "community"
    description: str = ""
    features: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.audience not in AUDIENCES:
            raise ValueError(f"plan {self.key}: audience must be one of {', '.join(AUDIENCES)}")
        bad = set(self.exports) - set(EXPORT_FORMATS)
        if bad:
            raise ValueError(f"plan {self.key}: unknown export formats {sorted(bad)}")

    def allows_model(self, model_type: str) -> bool:
        return ALL in self.model_types or model_type in self.model_types

    def available_to(self, tenant_kind: str) -> bool:
        return self.audience in ("any", tenant_kind)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("model_types", "exports", "features"):
            d[key] = list(d[key])
        return d


_CORE_MODELS = ("three-statement", "budget", "paas", "b2b-saas", "b2c-subscription")

DEFAULT_PLANS: dict[str, Plan] = {
    p.key: p
    for p in (
        # ---- B2C: individuals (founders, students, analysts) -------------------------
        Plan(
            "free",
            "Free",
            audience="individual",
            price_monthly=0,
            runs_per_month=100,
            max_periods=60,
            model_types=_CORE_MODELS,
            exports=("json",),
            api_keys=1,
            description="Model your own business: three-statement, budget, PaaS, B2B and B2C templates.",
        ),
        Plan(
            "pro",
            "Pro",
            audience="individual",
            price_monthly=19,
            price_yearly=190,
            runs_per_month=5_000,
            max_periods=120,
            exports=EXPORT_FORMATS,
            charts=True,
            data_fetch=True,
            api_keys=3,
            support="email",
            description="Every model (DCF, LBO, M&A), Excel/CSV export, charts and market data.",
        ),
        # ---- B2B: organisations (finance teams, advisors, funds) ---------------------
        Plan(
            "team",
            "Team",
            audience="organization",
            price_monthly=49,
            price_yearly=490,
            per_seat=True,
            seats=25,
            runs_per_month=50_000,
            max_periods=240,
            exports=EXPORT_FORMATS,
            charts=True,
            data_fetch=True,
            api_keys=10,
            support="email",
            description="Shared workspace with roles, per-seat billing and API access for your tools.",
        ),
        Plan(
            "business",
            "Business",
            audience="organization",
            price_monthly=999,
            price_yearly=9_990,
            seats=100,
            runs_per_month=500_000,
            max_periods=None,
            exports=EXPORT_FORMATS,
            charts=True,
            data_fetch=True,
            api_keys=50,
            support="priority",
            description="High-volume API for embedding models in your product (PaaS), priority support.",
        ),
        Plan(
            "enterprise",
            "Enterprise",
            audience="organization",
            price_monthly=None,
            seats=None,
            runs_per_month=None,
            max_periods=None,
            exports=EXPORT_FORMATS,
            charts=True,
            data_fetch=True,
            api_keys=None,
            support="dedicated",
            description="Unlimited use, private deployment or on-premise licence, SLA and custom models.",
        ),
    )
}

_FIELD_NAMES = {f.name for f in fields(Plan)}


def load_plans(overrides: dict[str, Any] | None = None) -> dict[str, Plan]:
    """Default catalogue with settings overrides applied (``platform.plans``)."""
    plans = dict(DEFAULT_PLANS)
    for key, spec in (overrides or {}).items():
        if spec is None:  # `plans: {free: null}` removes a tier
            plans.pop(key, None)
            continue
        if not isinstance(spec, dict):
            raise ValueError(f"platform.plans.{key} must be a mapping")
        unknown = set(spec) - _FIELD_NAMES
        if unknown:
            raise ValueError(f"platform.plans.{key}: unknown fields {sorted(unknown)}")
        base: dict[str, Any] = asdict(plans[key]) if key in plans else {"name": key.title()}
        base.update(spec)
        base["key"] = key
        for tuple_field in ("model_types", "exports", "features"):
            if tuple_field in base and not isinstance(base[tuple_field], tuple):
                base[tuple_field] = tuple(base[tuple_field])
        plans[key] = Plan(**base)
    return plans
