"""
The commercial platform's business logic, independent of HTTP.

* **Tenants** are the billable accounts. An ``individual`` tenant is a B2C self-serve
  account with one user; an ``organization`` tenant is a B2B team with members and roles.
* **Principals** are authenticated callers (API key or session token) bound to a tenant.
* **Entitlements** come from the tenant's plan: allowed model types, period limit,
  export formats, charts, data fetch, seats, keys and the monthly run quota.
* **Usage** is metered per tenant per calendar month (UTC) and is what usage-based
  pricing or overage invoices are built from.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from financial_model.platform.billing import BillingEvent, BillingProvider, ManualBilling
from financial_model.platform.plans import Plan, load_plans
from financial_model.platform.security import (
    digest_key,
    hash_password,
    key_prefix,
    new_api_key,
    sign_token,
    verify_password,
    verify_token,
)
from financial_model.platform.store import ApiKey, SQLiteStore, Store, Tenant, User, new_id, open_store

ROLES = ("owner", "admin", "member", "viewer")
_RANK = {role: i for i, role in enumerate(reversed(ROLES))}  # viewer 0 ... owner 3
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FREE_PLAN_FALLBACK = "free"


class PlatformError(Exception):
    status_code = 400


class AuthError(PlatformError):
    status_code = 401


class Forbidden(PlatformError):
    status_code = 403


class NotEntitled(PlatformError):
    """The tenant's plan does not include this feature (upgrade to use it)."""

    status_code = 402


class QuotaExceeded(PlatformError):
    status_code = 429


class NotFound(PlatformError):
    status_code = 404


class Conflict(PlatformError):
    status_code = 409


@dataclass
class Principal:
    tenant: Tenant
    plan: Plan
    user: User | None
    role: str
    via: str  # "api_key" | "token"
    key_id: str | None = None

    def require_role(self, minimum: str) -> None:
        if _RANK[self.role] < _RANK[minimum]:
            raise Forbidden(f"requires the {minimum} role (you are {self.role})")


def current_period(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).strftime("%Y-%m")


class PlatformService:
    def __init__(
        self,
        store: Store | None = None,
        plans: dict[str, Plan] | None = None,
        billing: BillingProvider | None = None,
        secret_key: str = "",
        token_ttl_hours: float = 24,
        password_iterations: int = 600_000,
        default_plan: str = "free",
        default_org_plan: str = "team",
        allow_signup: bool = True,
    ):
        self.store = store or SQLiteStore()
        self.plans = plans or load_plans()
        self.billing = billing or ManualBilling()
        # Without a configured secret, tokens are valid only for this process's lifetime.
        self.secret_key = secret_key or secrets.token_urlsafe(32)
        self.secret_is_ephemeral = not secret_key
        self.token_ttl = int(token_ttl_hours * 3600)
        self.password_iterations = int(password_iterations)
        self.default_plan = default_plan
        self.default_org_plan = default_org_plan
        self.allow_signup = allow_signup
        for key in (default_plan, default_org_plan):
            if key not in self.plans:
                raise ValueError(f"default plan '{key}' is not in the plan catalogue")

    @classmethod
    def from_settings(cls, platform_cfg: dict[str, Any]) -> PlatformService:
        from financial_model.platform.billing import billing_provider

        return cls(
            store=open_store(str(platform_cfg.get("database") or "sqlite:///:memory:")),
            plans=load_plans(platform_cfg.get("plans") or {}),
            billing=billing_provider(platform_cfg.get("billing") or {}),
            secret_key=str(platform_cfg.get("secret_key") or ""),
            token_ttl_hours=float(platform_cfg.get("token_ttl_hours", 24)),
            password_iterations=int(platform_cfg.get("password_iterations", 600_000)),
            default_plan=str(platform_cfg.get("default_plan") or "free"),
            default_org_plan=str(platform_cfg.get("default_org_plan") or "team"),
            allow_signup=bool(platform_cfg.get("allow_signup", True)),
        )

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    def plan_for(self, tenant: Tenant) -> Plan:
        if tenant.status == "canceled":  # lapsed accounts keep their data with free-tier limits
            return self.plans.get(FREE_PLAN_FALLBACK) or self.plans[self.default_plan]
        return self.plans.get(tenant.plan) or self.plans[self.default_plan]

    def create_user(self, email: str, password: str | None, name: str = "") -> User:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise PlatformError("a valid email address is required")
        if self.store.get_user_by_email(email):
            raise Conflict("an account with this email already exists")
        pw_hash = hash_password(password, self.password_iterations) if password else None
        user = User(new_id("usr"), email, name or email.split("@")[0], pw_hash, time.time())
        self.store.add_user(user)
        return user

    def _new_tenant(self, kind: str, name: str, plan: str) -> Tenant:
        if plan not in self.plans:
            raise NotFound(f"unknown plan '{plan}'")
        if not self.plans[plan].available_to(kind):
            raise PlatformError(f"plan '{plan}' is not available to {kind} accounts")
        tenant = Tenant(new_id("ten"), kind, name.strip() or "Workspace", plan, "active", None, time.time())
        self.store.add_tenant(tenant)
        return tenant

    def signup_individual(self, email: str, password: str, name: str = "") -> tuple[User, Tenant, str]:
        """B2C self-serve: a user, their personal tenant on the default plan, and a first API key."""
        if not self.allow_signup:
            raise Forbidden("self-serve sign-up is disabled on this platform")
        user = self.create_user(email, password, name)
        tenant = self._new_tenant("individual", user.name, self.default_plan)
        self.store.set_membership(tenant.id, user.id, "owner")
        key, _ = self.issue_key(tenant.id, user.id, "default")
        return user, tenant, key

    def create_organization(self, name: str, owner: User, plan: str | None = None) -> Tenant:
        """B2B: an organisation tenant with ``owner`` as its first member."""
        tenant = self._new_tenant("organization", name, plan or self.default_org_plan)
        self.store.set_membership(tenant.id, owner.id, "owner")
        return tenant

    def add_member(self, tenant_id: str, email: str, role: str = "member", password: str | None = None) -> User:
        if role not in ROLES:
            raise PlatformError(f"role must be one of {', '.join(ROLES)}")
        tenant = self._tenant(tenant_id)
        if tenant.kind != "organization":
            raise PlatformError("members can only be added to organization accounts")
        user = self.store.get_user_by_email(email.strip().lower())
        if user is None:
            user = self.create_user(email, password)
        if self.store.get_role(tenant_id, user.id) is None:
            seats = self.plan_for(tenant).seats
            if seats is not None and len(self.store.members(tenant_id)) >= seats:
                raise NotEntitled(f"the {self.plan_for(tenant).name} plan includes {seats} seats; upgrade to add more")
        self.store.set_membership(tenant_id, user.id, role)
        return user

    def remove_member(self, tenant_id: str, user_id: str) -> None:
        members = self.store.members(tenant_id)
        owners = [u.id for u, r in members if r == "owner"]
        if owners == [user_id]:
            raise PlatformError("cannot remove the last owner")
        self.store.remove_membership(tenant_id, user_id)

    def set_plan(self, tenant_id: str, plan: str, status: str = "active") -> Tenant:
        tenant = self._tenant(tenant_id)
        if plan not in self.plans:
            raise NotFound(f"unknown plan '{plan}'")
        if not self.plans[plan].available_to(tenant.kind):
            raise PlatformError(f"plan '{plan}' is not available to {tenant.kind} accounts")
        self.store.update_tenant(tenant_id, plan=plan, status=status)
        return self._tenant(tenant_id)

    def _tenant(self, tenant_id: str) -> Tenant:
        tenant = self.store.get_tenant(tenant_id)
        if tenant is None:
            raise NotFound(f"no tenant {tenant_id}")
        return tenant

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def issue_key(self, tenant_id: str, user_id: str | None, name: str = "api") -> tuple[str, ApiKey]:
        tenant = self._tenant(tenant_id)
        limit = self.plan_for(tenant).api_keys
        active = [k for k in self.store.list_keys(tenant_id) if k.revoked_at is None]
        if limit is not None and len(active) >= limit:
            raise NotEntitled(f"the {self.plan_for(tenant).name} plan allows {limit} active API key(s)")
        plain, prefix, digest = new_api_key()
        record = ApiKey(new_id("key"), tenant_id, user_id, prefix, digest, name, time.time())
        self.store.add_key(record)
        return plain, record

    def login(self, email: str, password: str, tenant_id: str | None = None) -> tuple[str, int, Tenant]:
        user = self.store.get_user_by_email(email.strip().lower())
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError("invalid email or password")
        tenants = self.store.tenants_for_user(user.id)
        if not tenants:
            raise AuthError("this user does not belong to any account")
        chosen = next((t for t, _ in tenants if t.id == tenant_id), None) if tenant_id else tenants[0][0]
        if chosen is None:
            raise Forbidden("you are not a member of that account")
        token, expires = sign_token({"uid": user.id, "tid": chosen.id}, self.secret_key, self.token_ttl)
        return token, expires, chosen

    def authenticate(self, api_key: str | None = None, bearer: str | None = None) -> Principal:
        if api_key:
            prefix = key_prefix(api_key)
            record = self.store.get_key_by_prefix(prefix) if prefix else None
            if (
                record is None
                or record.revoked_at is not None
                or not secrets.compare_digest(record.digest, digest_key(api_key))
            ):
                raise AuthError("invalid or revoked API key")
            tenant = self._tenant(record.tenant_id)
            user = self.store.get_user(record.user_id) if record.user_id else None
            role = (self.store.get_role(tenant.id, user.id) if user else None) or "member"
            self.store.touch_key(record.id)
            return Principal(tenant, self.plan_for(tenant), user, role, "api_key", record.id)
        if bearer:
            claims = verify_token(bearer, self.secret_key)
            if claims is None:
                raise AuthError("invalid or expired token")
            tenant = self._tenant(str(claims["tid"]))
            user = self.store.get_user(str(claims["uid"]))
            member_role = self.store.get_role(tenant.id, user.id) if user else None
            if user is None or member_role is None:
                raise AuthError("token user is no longer a member of this account")
            return Principal(tenant, self.plan_for(tenant), user, member_role, "token")
        raise AuthError("send an X-API-Key header or an Authorization: Bearer token")

    # ------------------------------------------------------------------ #
    # Entitlements and metering
    # ------------------------------------------------------------------ #
    def check(
        self,
        principal: Principal,
        *,
        model_type: str | None = None,
        periods: int | None = None,
        export: str | None = None,
        charts: bool = False,
        data_fetch: bool = False,
        runs: int = 1,
    ) -> None:
        tenant, plan = principal.tenant, principal.plan
        if tenant.status == "past_due":
            raise NotEntitled("payment is past due; update your billing details to continue")
        if principal.role == "viewer" and runs:
            raise Forbidden("viewers cannot run models")
        if model_type and not plan.allows_model(model_type):
            raise NotEntitled(f"the {plan.name} plan does not include '{model_type}' models")
        if periods is not None and plan.max_periods is not None and periods > plan.max_periods:
            raise NotEntitled(f"the {plan.name} plan allows up to {plan.max_periods} periods per model")
        if export and export not in plan.exports:
            raise NotEntitled(f"the {plan.name} plan does not include {export} export")
        if charts and not plan.charts:
            raise NotEntitled(f"the {plan.name} plan does not include charts")
        if data_fetch and not plan.data_fetch:
            raise NotEntitled(f"the {plan.name} plan does not include market-data fetch")
        if runs and plan.runs_per_month is not None:
            used = self.store.get_usage(tenant.id, current_period()).get("runs", 0)
            if used + runs > plan.runs_per_month:
                raise QuotaExceeded(
                    f"monthly quota of {plan.runs_per_month} runs reached on the {plan.name} plan; "
                    "upgrade or wait for the next month"
                )

    def record(self, tenant_id: str, metric: str = "runs", quantity: float = 1) -> None:
        self.store.add_usage(tenant_id, current_period(), metric, quantity)

    def usage(self, tenant_id: str, period: str | None = None) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        plan = self.plan_for(tenant)
        period = period or current_period()
        used = self.store.get_usage(tenant_id, period)
        return {
            "period": period,
            "plan": plan.key,
            "metrics": used,
            "runs_limit": plan.runs_per_month,
            "runs_remaining": None
            if plan.runs_per_month is None
            else max(plan.runs_per_month - int(used.get("runs", 0)), 0),
            "seats_used": len(self.store.members(tenant_id)),
            "seats_limit": plan.seats,
        }

    # ------------------------------------------------------------------ #
    # Billing
    # ------------------------------------------------------------------ #
    def checkout(self, principal: Principal, plan: str, success_url: str, cancel_url: str) -> str:
        principal.require_role("admin")
        if plan not in self.plans:
            raise NotFound(f"unknown plan '{plan}'")
        if not self.plans[plan].available_to(principal.tenant.kind):
            raise PlatformError(f"plan '{plan}' is not available to {principal.tenant.kind} accounts")
        seats = len(self.store.members(principal.tenant.id)) if self.plans[plan].per_seat else 1
        email = principal.user.email if principal.user else None
        return self.billing.checkout_url(principal.tenant.id, plan, email, success_url, cancel_url, seats)

    def apply_billing_events(self, events: list[BillingEvent]) -> list[str]:
        """Apply provider events (idempotent per event id); returns the tenant ids changed."""
        changed: list[str] = []
        for event in events:
            if not self.store.mark_event(event.id):
                continue
            tenant = self.store.get_tenant(event.tenant_id) if event.tenant_id else None
            if tenant is None and event.customer_id:
                tenant = self.store.find_tenant_by_customer(event.customer_id)
            if tenant is None:
                continue
            fields: dict[str, Any] = {}
            if event.customer_id and tenant.billing_customer_id != event.customer_id:
                fields["billing_customer_id"] = event.customer_id
            if event.plan and event.plan in self.plans and self.plans[event.plan].available_to(tenant.kind):
                fields["plan"] = event.plan
            if event.status:
                fields["status"] = event.status
                if event.status == "canceled":
                    fields["plan"] = self.default_plan if tenant.kind == "individual" else tenant.plan
            if fields:
                self.store.update_tenant(tenant.id, **fields)
                changed.append(tenant.id)
        return changed
