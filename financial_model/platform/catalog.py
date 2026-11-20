"""
The commercial catalogue as Python objects: plans, metering (pay-to-use) rules, roles,
account kinds, account statuses and per-endpoint rules.

Nothing commercial is defined in code. The built-in values live in ``catalog.yaml``
next to this module; an operator replaces or extends them from the settings file:

* ``platform.catalog``: list of catalogue files merged in order (``builtin`` = the
  shipped file). Default ``[builtin]``.
* Any catalogue key set inline under ``platform:`` (``plans``, ``roles``, ``endpoints``,
  ``metrics``, ``currency``...) is deep-merged on top; ``null`` deletes an entry.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BUILTIN = "builtin"
BUILTIN_PATH = Path(__file__).with_name("catalog.yaml")
SETTINGS_PATH = Path(__file__).with_name("settings.yaml")
WILDCARD = "*"

#: Top-level catalogue keys (also accepted inline under ``platform:`` in the settings).
CATALOG_KEYS = (
    "currency",
    "account_kinds",
    "signup_kind",
    "default_account_kind",
    "default_plan",
    "lapsed_plan",
    "roles",
    "creator_role",
    "default_member_role",
    "service_key_role",
    "account_statuses",
    "initial_status",
    "metrics",
    "default_metering",
    "default_endpoint",
    "endpoints",
    "plans",
)


def builtin_settings() -> dict[str, Any]:
    """The shipped runtime defaults of the ``platform:`` settings section (settings.yaml)."""
    with SETTINGS_PATH.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return data


class CatalogError(ValueError):
    """The catalogue is inconsistent (unknown plan, role, metric...)."""


# --------------------------------------------------------------------------- #
# Metering: what a unit of usage costs, and when it is refused
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tier:
    up_to: float | None  # cumulative billable quantity this tier ends at (None = no end)
    unit_price: float


@dataclass(frozen=True)
class MeterRule:
    metric: str
    included: float | None = 0  # None = unlimited
    overage: str = "block"  # block | bill
    tiers: tuple[Tier, ...] = ()
    cap: float | None = None

    OVERAGE_MODES = ("block", "bill")

    @classmethod
    def from_dict(cls, metric: str, spec: dict[str, Any]) -> MeterRule:
        unknown = set(spec) - {"included", "overage", "unit_price", "tiers", "cap"}
        if unknown:
            raise CatalogError(f"metering.{metric}: unknown fields {sorted(unknown)}")
        overage = str(spec.get("overage", "block"))
        if overage not in cls.OVERAGE_MODES:
            raise CatalogError(f"metering.{metric}.overage must be one of {', '.join(cls.OVERAGE_MODES)}")
        tiers: list[Tier] = []
        if spec.get("tiers"):
            tiers = [Tier(t.get("up_to"), float(t["unit_price"])) for t in spec["tiers"]]
        elif spec.get("unit_price") is not None:
            tiers = [Tier(None, float(spec["unit_price"]))]
        if overage == "bill" and not tiers:
            raise CatalogError(f"metering.{metric}: overage 'bill' needs unit_price or tiers")
        included = spec.get("included", 0)
        return cls(
            metric,
            None if included is None else float(included),
            overage,
            tuple(tiers),
            None if spec.get("cap") is None else float(spec["cap"]),
        )

    @property
    def unlimited(self) -> bool:
        return self.included is None

    def refusal(self, used: float, quantity: float) -> str | None:
        """Why ``quantity`` more units would be refused, or None if allowed."""
        if self.unlimited:
            return None
        after = used + quantity
        if self.cap is not None and after > self.cap:
            return f"hard cap of {self.cap:g} {self.metric} per month reached"
        if self.overage == "block" and after > (self.included or 0):
            if not self.included:
                return f"{self.metric} are not included in this plan"
            return f"monthly allowance of {self.included:g} {self.metric} reached"
        return None

    def billable(self, used: float) -> float:
        return 0.0 if self.unlimited else max(used - (self.included or 0), 0.0)

    def charge(self, used: float) -> tuple[float, list[dict[str, float | None]]]:
        """Overage charge for ``used`` units in a month, with the per-tier breakdown."""
        remaining = self.billable(used)
        total, start = 0.0, 0.0
        lines: list[dict[str, float | None]] = []
        for tier in self.tiers:
            if remaining <= 0:
                break
            width = remaining if tier.up_to is None else max(min(remaining, tier.up_to - start), 0.0)
            if width > 0:
                amount = width * tier.unit_price
                lines.append({"quantity": width, "unit_price": tier.unit_price, "amount": amount})
                total += amount
                remaining -= width
            if tier.up_to is not None:
                start = tier.up_to
        return total, lines

    def remaining(self, used: float) -> float | None:
        if self.unlimited:
            return None
        if self.overage == "bill":
            return None if self.cap is None else max(self.cap - used, 0.0)
        return max((self.included or 0) - used, 0.0)


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
_PLAN_FIELDS = {"name", "audience", "price", "limits", "model_types", "features", "metering", "display"}


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    audience: tuple[str, ...]
    price_monthly: float | None
    price_yearly: float | None
    per_seat: bool
    limits: dict[str, float | None]
    model_types: tuple[str, ...]
    features: frozenset[str]
    metering: dict[str, MeterRule]
    display: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, key: str, spec: dict[str, Any], default_metering: dict[str, Any]) -> Plan:
        unknown = set(spec) - _PLAN_FIELDS
        if unknown:
            raise CatalogError(f"plans.{key}: unknown fields {sorted(unknown)} (free-form data goes in `display`)")
        price = spec.get("price") or {}
        metering = {m: MeterRule.from_dict(m, r or {}) for m, r in (spec.get("metering") or {}).items()}
        return cls(
            key=key,
            name=str(spec.get("name") or key.title()),
            audience=tuple(spec.get("audience") or ()),
            price_monthly=price.get("monthly"),
            price_yearly=price.get("yearly"),
            per_seat=bool(price.get("per_seat", False)),
            limits=dict(spec.get("limits") or {}),
            model_types=tuple(spec.get("model_types") or (WILDCARD,)),
            features=frozenset(spec.get("features") or ()),
            metering=metering,
            display=dict(spec.get("display") or {}),
            raw={**copy.deepcopy(spec), "_default_metering": default_metering},
        )

    def rule(self, metric: str) -> MeterRule:
        if metric in self.metering:
            return self.metering[metric]
        return MeterRule.from_dict(metric, self.raw.get("_default_metering") or {})

    def limit(self, name: str) -> float | None:
        """A numeric limit; missing or null means unlimited."""
        value = self.limits.get(name)
        return None if value is None else float(value)

    def allows_model(self, model_type: str) -> bool:
        return WILDCARD in self.model_types or model_type in self.model_types

    def has_features(self, required: list[str] | tuple[str, ...]) -> list[str]:
        """Required features this plan lacks."""
        if WILDCARD in self.features:
            return []
        return [f for f in required if f not in self.features]

    def available_to(self, kind: str) -> bool:
        return WILDCARD in self.audience or kind in self.audience

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in copy.deepcopy(self.raw).items() if not k.startswith("_")}
        data["key"] = self.key
        data["name"] = self.name
        return data


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load(path: str | Path) -> dict[str, Any]:
    p = BUILTIN_PATH if str(path) == BUILTIN else Path(path)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise CatalogError(f"{p}: a catalogue must be a mapping")
    return data


@dataclass
class Catalog:
    data: dict[str, Any]
    plans: dict[str, Plan] = field(init=False)

    def __post_init__(self) -> None:
        d = self.data
        missing = [k for k in ("account_kinds", "roles", "plans", "account_statuses") if not d.get(k)]
        if missing:
            raise CatalogError(f"catalogue is missing {', '.join(missing)}")
        default_metering = d.get("default_metering") or {"included": 0, "overage": "block"}
        self.plans = {k: Plan.from_dict(k, v or {}, default_metering) for k, v in d["plans"].items()}
        self._validate()

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, sources: list[str | Path] | None = None, overrides: dict[str, Any] | None = None) -> Catalog:
        data: dict[str, Any] = {}
        for src in sources or [BUILTIN]:
            data = _deep_merge(data, _load(src))
        return cls(_deep_merge(data, overrides or {}))

    @classmethod
    def from_settings(cls, platform_cfg: dict[str, Any]) -> Catalog:
        sources = platform_cfg.get("catalog") or [BUILTIN]
        if isinstance(sources, (str, Path)):
            sources = [sources]
        overrides = {k: platform_cfg[k] for k in CATALOG_KEYS if k in platform_cfg}
        # Inline `plans: {free: null}` must delete: keep None values for the merge.
        return cls.load(list(sources), overrides)

    # -- accessors -----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def currency(self) -> str:
        return str(self.data.get("currency", ""))

    def kind(self, name: str) -> dict[str, Any]:
        spec = self.data["account_kinds"].get(name)
        if spec is None:
            raise CatalogError(f"unknown account kind '{name}'")
        return dict(spec)

    def role(self, name: str) -> dict[str, Any]:
        spec = self.data["roles"].get(name)
        if spec is None:
            raise CatalogError(f"unknown role '{name}'")
        return dict(spec)

    def rank(self, role: str) -> float:
        return float(self.role(role).get("rank", 0))

    def permits(self, role: str, permission: str) -> bool:
        perms = self.role(role).get("permissions") or []
        return WILDCARD in perms or permission in perms

    def status(self, name: str) -> dict[str, Any]:
        spec = self.data["account_statuses"].get(name)
        if spec is None:
            raise CatalogError(f"unknown account status '{name}'")
        return dict(spec)

    def default_plan_for(self, kind: str) -> str:
        default = self.data.get("default_plan")
        key = default.get(kind) if isinstance(default, dict) else default
        if not key:
            raise CatalogError(f"no default_plan for account kind '{kind}'")
        return str(key)

    def endpoint(self, method: str, path: str) -> dict[str, Any]:
        spec = (self.data.get("endpoints") or {}).get(f"{method.upper()} {path}")
        if spec is None:
            spec = self.data.get("default_endpoint") or {}
        return dict(spec)

    # -- validation ----------------------------------------------------------
    def _validate(self) -> None:
        d = self.data
        kinds, roles = set(d["account_kinds"]), set(d["roles"])
        for ref in ("creator_role", "default_member_role", "service_key_role"):
            if d.get(ref) not in roles:
                raise CatalogError(f"{ref} '{d.get(ref)}' is not a defined role")
        for ref in ("signup_kind", "default_account_kind"):
            if d.get(ref) is not None and d[ref] not in kinds:
                raise CatalogError(f"{ref} '{d[ref]}' is not a defined account kind")
        if d.get("initial_status") not in d["account_statuses"]:
            raise CatalogError(f"initial_status '{d.get('initial_status')}' is not a defined account status")
        for kind in kinds:
            plan = self.default_plan_for(kind)
            if plan not in self.plans:
                raise CatalogError(f"default_plan for {kind} is '{plan}', which is not a plan")
        lapsed = d.get("lapsed_plan")
        if lapsed is not None and lapsed not in self.plans:
            raise CatalogError(f"lapsed_plan '{lapsed}' is not a plan")
        metrics = set(d.get("metrics") or {})
        for p in self.plans.values():
            bad = set(p.audience) - kinds - {WILDCARD}
            if bad:
                raise CatalogError(f"plans.{p.key}.audience: unknown account kinds {sorted(bad)}")
            if metrics and set(p.metering) - metrics:
                raise CatalogError(f"plans.{p.key}.metering: unknown metrics {sorted(set(p.metering) - metrics)}")
        for route, spec in (d.get("endpoints") or {}).items():
            if spec and spec.get("metric") and metrics and spec["metric"] not in metrics:
                raise CatalogError(f"endpoints['{route}'].metric '{spec['metric']}' is not a defined metric")
