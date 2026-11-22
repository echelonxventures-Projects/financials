"""
The commercial platform's business logic, independent of HTTP.

Every commercial rule it applies (plans, prices, pay-to-use metering, quotas, seats,
roles and permissions, account kinds and statuses) comes from the
:class:`~financial_model.platform.catalog.Catalog`, and every runtime default from
``platform/settings.yaml``; nothing is hard-coded here.

* **Accounts** (tenants) are what is billed. Their *kind* (e.g. ``individual`` for B2C,
  ``organization`` for B2B) comes from the catalogue.
* **Principals** are authenticated callers (API key or session token) bound to an account.
* **Usage** is metered per account per calendar month (UTC); plans either cap it or
  price it (pay to use), and :meth:`PlatformService.invoice` turns it into charges.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from financial_model.platform.billing import BillingEvent, BillingProvider, billing_provider
from financial_model.platform.catalog import Catalog, Plan, builtin_settings
from financial_model.platform.email import EmailService, EmailTemplateStore, email_provider
from financial_model.platform.legal import LegalDocument, LegalStore, validate_legal_content
from financial_model.platform.security import KeyFormat, hash_password, sign_token, verify_password, verify_token
from financial_model.platform.sso import SSOService, SSOStore, sso_settings_from_config, build_sso_provider
from financial_model.platform.store import ApiKey, Store, Tenant, User, new_id, open_store

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REPORTED = "reported:"  # usage-metric prefix for quantities already reported to the billing provider


class PlatformError(Exception):
    status_code = 400


class AuthError(PlatformError):
    status_code = 401


class Forbidden(PlatformError):
    status_code = 403


class NotEntitled(PlatformError):
    """The account's plan does not include this (upgrade to use it)."""

    status_code = 402


class QuotaExceeded(PlatformError):
    status_code = 429


class TooManyAttempts(PlatformError):
    """Too many failed sign-ins; ``retry_after`` is in seconds."""

    status_code = 429

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class ConfigurationError(PlatformError):
    """The platform settings are not safe to serve with."""

    status_code = 500


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
    catalog: Catalog
    key_id: str | None = None

    def can(self, permission: str) -> bool:
        return self.catalog.permits(self.role, permission)

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise Forbidden(self.catalog.message("role_lacks_permission", role=self.role, permission=permission))


def current_period(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).strftime("%Y-%m")


class PlatformService:
    """Accounts, credentials, entitlements, metering and billing for one platform.

    Arguments left as ``None`` take their value from the built-in ``settings.yaml``.
    """

    def __init__(
        self,
        store: Store | None = None,
        catalog: Catalog | None = None,
        billing: BillingProvider | None = None,
        secret_key: str | None = None,
        token_ttl_hours: float | None = None,
        password_iterations: int | None = None,
        password_min_length: int | None = None,
        api_key_prefix: str | None = None,
        allow_signup: bool | None = None,
        require_secret_key: bool | None = None,
        secret_key_min_length: int | None = None,
        login_throttle: dict[str, Any] | None = None,
        # Config dict (merged platform settings) - overrides builtin_settings()
        config: dict[str, Any] | None = None,
        # New platform features (injected for testing)
        legal_store: LegalStore | None = None,
        email_service: EmailService | None = None,
        sso_service: SSOService | None = None,
    ):
        base = config or builtin_settings()

        def pick(value: Any, key: str) -> Any:
            return base[key] if value is None else value

        self.store = store or open_store("sqlite:///:memory:")
        self.catalog = catalog or Catalog.from_settings(base)
        self.billing = billing or billing_provider(base.get("billing") or {})
        secret = pick(secret_key, "secret_key")
        # Without a configured secret, tokens are valid only for this process's lifetime.
        self.secret_key = secret or secrets.token_urlsafe(32)
        self.secret_is_ephemeral = not secret
        self.token_ttl = int(float(pick(token_ttl_hours, "token_ttl_hours")) * 3600)
        self.password_iterations = int(pick(password_iterations, "password_iterations"))
        self.password_min_length = int(pick(password_min_length, "password_min_length"))
        self.keys = KeyFormat(str(pick(api_key_prefix, "api_key_prefix")))
        self.allow_signup = bool(pick(allow_signup, "allow_signup"))
        self.require_secret_key = bool(pick(require_secret_key, "require_secret_key"))
        self.secret_key_min_length = int(pick(secret_key_min_length, "secret_key_min_length"))
        self._configured_secret = str(secret or "")
        throttle = {**(base.get("login_throttle") or {}), **(login_throttle or {})}
        self.login_window = float(throttle.get("window_seconds") or 0)
        self.login_limits = {
            "email": int(throttle.get("max_failures_per_email") or 0),
            "address": int(throttle.get("max_failures_per_address") or 0),
        }
        self._dummy_hash: str | None = None

        # Read feature flags and storage paths from new minimal settings
        features = base.get("features", {})
        storage = base.get("storage", {})

        # --- Legal documents ---
        self.legal_enabled = bool(features.get("legal", True))
        if self.legal_enabled:
            self.legal = legal_store or LegalStore(storage.get("legal_path", "./legal_documents"))
            # Template vars now managed via Admin API - use defaults for builtins
            template_vars = {
                "effective_date": "",
                "domain": "your-domain.com",
                "controller": "Customer",
                "processor": "Platform Provider",
                "controller_name": "Customer Name",
                "processor_name": "Platform Provider Name",
                "subprocessor_url": "https://your-domain.com/subprocessors",
            }
            self.legal.ensure_builtins(template_vars, auto_approve=True)
            self.legal_require_acceptance = True
        else:
            self.legal = None
            self.legal_require_acceptance = False

        # --- Email service ---
        self.email_enabled = bool(features.get("email", True))
        if self.email_enabled:
            # Provider settings are now managed via Admin API
            # Use console as default, can be overridden via Admin Portal
            provider = email_provider({"provider": "console"})
            templates = EmailTemplateStore(storage.get("email_templates_path", "./email_templates"))
            templates.ensure_builtins()
            self.email = EmailService(
                provider=provider,
                templates=templates,
                default_from_email="noreply@your-domain.com",
                default_from_name="Platform",
                platform_name="Platform",
            )
        else:
            self.email = None

        # --- SSO service ---
        self.sso_enabled = bool(features.get("sso", False))
        if self.sso_enabled:
            sso_store = SSOStore(storage.get("sso_providers_path", "./sso_providers"))
            sso_store.ensure_builtins()
            self.sso = SSOService(sso_store, self)
        else:
            self.sso = None

    def check_serving_config(self) -> None:
        """Raise :class:`ConfigurationError` if these settings are unsafe to serve the platform with."""
        if self.require_secret_key and len(self._configured_secret) < self.secret_key_min_length:
            raise ConfigurationError(self.catalog.message("secret_key_required", length=self.secret_key_min_length))

    @property
    def plans(self) -> dict[str, Plan]:
        return self.catalog.plans

    @classmethod
    def from_settings(cls, platform_cfg: dict[str, Any]) -> PlatformService:
        """Build from the ``platform:`` settings section (missing keys: settings.yaml defaults)."""
        from financial_model.config_loader import _deep_merge

        cfg = _deep_merge(builtin_settings(), platform_cfg)
        return cls(
            store=open_store(str(cfg["database"])),
            catalog=Catalog.from_settings(cfg),
            billing=billing_provider(cfg.get("billing") or {}),
            secret_key=str(cfg.get("secret_key") or ""),
            token_ttl_hours=float(cfg["token_ttl_hours"]),
            password_iterations=int(cfg["password_iterations"]),
            password_min_length=int(cfg["password_min_length"]),
            api_key_prefix=str(cfg["api_key_prefix"]),
            allow_signup=bool(cfg["allow_signup"]),
            require_secret_key=bool(cfg["require_secret_key"]),
            secret_key_min_length=int(cfg["secret_key_min_length"]),
            login_throttle=dict(cfg.get("login_throttle") or {}),
            config=cfg,  # Pass merged config for feature flags and storage paths
        )

    # ------------------------------------------------------------------ #
    # Plans and accounts
    # ------------------------------------------------------------------ #
    def plan_for(self, tenant: Tenant) -> Plan:
        status = self.catalog.status(tenant.status)
        lapsed = self.catalog.get("lapsed_plan")
        if status.get("use_lapsed_plan") and lapsed in self.plans:
            return self.plans[lapsed]
        if tenant.plan in self.plans:
            return self.plans[tenant.plan]
        return self.plans[self.catalog.default_plan_for(tenant.kind)]

    def _plan(self, key: str, kind: str) -> Plan:
        plan = self.plans.get(key)
        if plan is None:
            raise NotFound(self.catalog.message("unknown_plan", plan=key))
        if not plan.available_to(kind):
            raise PlatformError(self.catalog.message("plan_not_for_kind", plan=key, kind=kind))
        return plan

    def seat_limit(self, tenant: Tenant) -> float | None:
        limits = [self.plan_for(tenant).limit("seats"), self.catalog.kind(tenant.kind).get("max_members")]
        known = [float(x) for x in limits if x is not None]
        return min(known) if known else None

    def create_user(self, email: str, password: str | None, name: str = "") -> User:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise PlatformError(self.catalog.message("invalid_email"))
        if self.store.get_user_by_email(email):
            raise Conflict(self.catalog.message("email_taken"))
        pw_hash = hash_password(password, self.password_iterations, self.password_min_length) if password else None
        user = User(new_id("usr"), email, name or email.split("@")[0], pw_hash, time.time())
        self.store.add_user(user)
        return user

    def create_account(self, kind: str, name: str, owner: User | None, plan: str | None = None) -> Tenant:
        label = str(self.catalog.kind(kind).get("label") or kind)
        plan_key = plan or self.catalog.default_plan_for(kind)
        self._plan(plan_key, kind)
        tenant = Tenant(
            new_id("ten"),
            kind,
            name.strip() or label,
            plan_key,
            str(self.catalog.get("initial_status")),
            None,
            time.time(),
        )
        self.store.add_tenant(tenant)
        if owner is not None:
            self.store.set_membership(tenant.id, owner.id, str(self.catalog.get("creator_role")))
        return tenant

    def signup(self, email: str, password: str, name: str = "") -> tuple[User, Tenant, str]:
        """Self-serve sign-up (B2C by default): a user, an account of ``signup_kind`` and a first API key."""
        kind = self.catalog.get("signup_kind")
        if not self.allow_signup or not kind or not self.catalog.kind(kind).get("signup"):
            raise Forbidden(self.catalog.message("signup_disabled"))
        user = self.create_user(email, password, name)
        tenant = self.create_account(kind, user.name, user)
        key, _ = self.issue_key(tenant.id, user.id, "default")
        return user, tenant, key

    def create_user_account(self, user: User, name: str, kind: str | None = None, plan: str | None = None) -> Tenant:
        """An account created by a logged-in user (B2B organisations by default)."""
        kind = kind or self.catalog.get("default_account_kind")
        if not kind or not self.catalog.kind(kind).get("user_creatable"):
            raise Forbidden(self.catalog.message("kind_not_user_creatable", kind=kind))
        return self.create_account(kind, name, user, plan)

    def add_member(self, tenant_id: str, email: str, role: str | None = None, password: str | None = None) -> User:
        role = role or str(self.catalog.get("default_member_role"))
        self.catalog.role(role)
        tenant = self._tenant(tenant_id)
        user = self.store.get_user_by_email(email.strip().lower())
        if user is None:
            user = self.create_user(email, password)
        if self.store.get_role(tenant_id, user.id) is None:
            seats = self.seat_limit(tenant)
            if seats is not None and len(self.store.members(tenant_id)) >= seats:
                raise NotEntitled(self.catalog.message("seat_limit", seats=seats, plan=self.plan_for(tenant).name))
        self.store.set_membership(tenant_id, user.id, role)
        return user

    def remove_member(self, tenant_id: str, user_id: str) -> None:
        creator = self.catalog.get("creator_role")
        holders = [u.id for u, r in self.store.members(tenant_id) if r == creator]
        if holders == [user_id]:
            raise PlatformError(self.catalog.message("last_creator", role=creator))
        self.store.remove_membership(tenant_id, user_id)

    def set_plan(self, tenant_id: str, plan: str, status: str | None = None) -> Tenant:
        tenant = self._tenant(tenant_id)
        self._plan(plan, tenant.kind)
        status = status or str(self.catalog.get("initial_status"))
        self.catalog.status(status)
        self.store.update_tenant(tenant_id, plan=plan, status=status)
        return self._tenant(tenant_id)

    def _tenant(self, tenant_id: str) -> Tenant:
        tenant = self.store.get_tenant(tenant_id)
        if tenant is None:
            raise NotFound(self.catalog.message("unknown_account", account=tenant_id))
        return tenant

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def issue_key(self, tenant_id: str, user_id: str | None, name: str = "api") -> tuple[str, ApiKey]:
        tenant = self._tenant(tenant_id)
        limit = self.plan_for(tenant).limit("api_keys")
        active = [k for k in self.store.list_keys(tenant_id) if k.revoked_at is None]
        if limit is not None and len(active) >= limit:
            raise NotEntitled(self.catalog.message("key_limit", plan=self.plan_for(tenant).name, limit=limit))
        plain, prefix, digest = self.keys.new()
        record = ApiKey(new_id("key"), tenant_id, user_id, prefix, digest, name, time.time())
        self.store.add_key(record)
        return plain, record

    def _throttle_keys(self, email: str, address: str | None) -> list[tuple[str, int]]:
        keys = [(f"email:{email}", self.login_limits["email"])]
        if address:
            keys.append((f"address:{address}", self.login_limits["address"]))
        return [(k, limit) for k, limit in keys if limit > 0 and self.login_window > 0]

    def _check_throttle(self, keys: list[tuple[str, int]], now: float) -> None:
        for key, limit in keys:
            count, oldest = self.store.count_login_failures(key, now - self.login_window)
            if count >= limit:
                wait = max(1, int((oldest or now) + self.login_window - now + 0.999))
                raise TooManyAttempts(self.catalog.message("too_many_logins", seconds=wait), wait)

    def _password_ok(self, password: str, user: User | None) -> bool:
        if user is not None and user.password_hash:
            return verify_password(password, user.password_hash)
        # Same work for unknown emails, so response time does not reveal which emails exist.
        if self._dummy_hash is None:
            self._dummy_hash = hash_password(secrets.token_hex(8), self.password_iterations)
        verify_password(password, self._dummy_hash)
        return False

    def login(
        self, email: str, password: str, tenant_id: str | None = None, address: str | None = None
    ) -> tuple[str, int, Tenant]:
        email = email.strip().lower()
        keys = self._throttle_keys(email, address)
        now = time.time()
        self._check_throttle(keys, now)
        user = self.store.get_user_by_email(email)
        if not self._password_ok(password, user) or user is None:
            for key, _ in keys:
                self.store.add_login_failure(key, now, prune_before=now - self.login_window)
            raise AuthError(self.catalog.message("bad_login"))
        self.store.clear_login_failures(f"email:{email}")
        tenants = self.store.tenants_for_user(user.id)
        if not tenants:
            raise AuthError(self.catalog.message("no_accounts"))
        chosen = next((t for t, _ in tenants if t.id == tenant_id), None) if tenant_id else tenants[0][0]
        if chosen is None:
            raise Forbidden(self.catalog.message("not_member"))
        # Check legal acceptance
        principal = self._principal(chosen, user, self.store.get_role(chosen.id, user.id) or "member", "token")
        self.check_legal_acceptance(principal)
        token, expires = sign_token({"uid": user.id, "tid": chosen.id}, self.secret_key, self.token_ttl)
        return token, expires, chosen

    def _principal(
        self, tenant: Tenant, user: User | None, role: str, via: str, key_id: str | None = None
    ) -> Principal:
        return Principal(tenant, self.plan_for(tenant), user, role, via, self.catalog, key_id)

    def authenticate(self, api_key: str | None = None, bearer: str | None = None, skip_legal_check: bool = False) -> Principal:
        if api_key:
            prefix = self.keys.prefix_of(api_key)
            record = self.store.get_key_by_prefix(prefix) if prefix else None
            if (
                record is None
                or record.revoked_at is not None
                or not secrets.compare_digest(record.digest, self.keys.digest(api_key))
            ):
                raise AuthError(self.catalog.message("bad_key"))
            tenant = self._tenant(record.tenant_id)
            user = self.store.get_user(record.user_id) if record.user_id else None
            role = (self.store.get_role(tenant.id, user.id) if user else None) or str(
                self.catalog.get("service_key_role")
            )
            self.store.touch_key(record.id)
            principal = self._principal(tenant, user, role, "api_key", record.id)
            if not skip_legal_check:
                self.check_legal_acceptance(principal)
            return principal
        if bearer:
            claims = verify_token(bearer, self.secret_key)
            if claims is None:
                raise AuthError(self.catalog.message("bad_token"))
            tenant = self._tenant(str(claims["tid"]))
            user = self.store.get_user(str(claims["uid"]))
            member_role = self.store.get_role(tenant.id, user.id) if user else None
            if user is None or member_role is None:
                raise AuthError(self.catalog.message("token_member_removed"))
            principal = self._principal(tenant, user, member_role, "token")
            if not skip_legal_check:
                self.check_legal_acceptance(principal)
            return principal
        raise AuthError(self.catalog.message("no_credentials"))

    # ------------------------------------------------------------------ #
    # Entitlements and metering
    # ------------------------------------------------------------------ #
    def check(
        self,
        principal: Principal,
        *,
        permission: str | None = None,
        features: list[str] | tuple[str, ...] = (),
        model_type: str | None = None,
        periods: int | None = None,
        metric: str | None = None,
        quantity: float = 1,
    ) -> None:
        tenant, plan = principal.tenant, principal.plan
        if permission:
            principal.require(permission)
        status = self.catalog.status(tenant.status)
        if metric and status.get("blocked"):
            raise NotEntitled(
                str(status.get("message") or self.catalog.message("status_blocked", status=tenant.status))
            )
        if model_type and not plan.allows_model(model_type):
            raise NotEntitled(self.catalog.message("model_not_in_plan", plan=plan.name, model_type=model_type))
        max_periods = plan.limit("periods")
        if periods is not None and max_periods is not None and periods > max_periods:
            raise NotEntitled(self.catalog.message("period_limit", plan=plan.name, limit=max_periods))
        missing = plan.has_features(features)
        if missing:
            raise NotEntitled(self.catalog.message("features_not_in_plan", plan=plan.name, features=", ".join(missing)))
        if metric:
            used = self.store.get_usage(tenant.id, current_period()).get(metric, 0.0)
            refusal = plan.rule(metric).refusal(used, quantity)
            if refusal:
                reason = self.catalog.message(refusal[0], **refusal[1])
                raise QuotaExceeded(self.catalog.message("quota_exceeded", reason=reason, plan=plan.name))

    def record(self, tenant_id: str, metric: str, quantity: float = 1) -> None:
        self.store.add_usage(tenant_id, current_period(), metric, quantity)

    def _metrics(self, tenant_id: str, period: str) -> dict[str, float]:
        return {k: v for k, v in self.store.get_usage(tenant_id, period).items() if not k.startswith(REPORTED)}

    def usage(self, tenant_id: str, period: str | None = None) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        plan = self.plan_for(tenant)
        period = period or current_period()
        used = self._metrics(tenant_id, period)
        names = sorted(set(self.catalog.get("metrics") or {}) | set(plan.metering) | set(used))
        allowances = {}
        for m in names:
            rule, qty = plan.rule(m), used.get(m, 0.0)
            allowances[m] = {
                "used": qty,
                "included": rule.included,
                "overage": rule.overage,
                "remaining": rule.remaining(qty),
            }
        return {
            "period": period,
            "plan": plan.key,
            "metrics": used,
            "allowances": allowances,
            "seats_used": len(self.store.members(tenant_id)),
            "seats_limit": self.seat_limit(tenant),
        }

    def invoice(self, tenant_id: str, period: str | None = None) -> dict[str, Any]:
        """Charges for a month from the plan's prices and pay-to-use rules (an invoice preview)."""
        tenant = self._tenant(tenant_id)
        plan = self.plan_for(tenant)
        period = period or current_period()
        used = self._metrics(tenant_id, period)
        metric_specs = self.catalog.get("metrics") or {}
        lines: list[dict[str, Any]] = []
        if plan.price_monthly:
            qty = len(self.store.members(tenant_id)) if plan.per_seat else 1
            lines.append(
                {
                    "item": plan.name,
                    "per_seat": plan.per_seat,
                    "quantity": qty,
                    "unit_price": plan.price_monthly,
                    "amount": qty * plan.price_monthly,
                }
            )
        for metric, quantity in sorted(used.items()):
            amount, tiers = plan.rule(metric).charge(quantity)
            if amount:
                label = (metric_specs.get(metric) or {}).get("label", metric)
                lines.append({"item": label, "metric": metric, "used": quantity, "tiers": tiers, "amount": amount})
        return {
            "account": tenant.id,
            "period": period,
            "plan": plan.key,
            "currency": self.catalog.currency,
            "contact_sales": plan.price_monthly is None,
            "lines": lines,
            "total": sum(float(line["amount"]) for line in lines),
        }

    # ------------------------------------------------------------------ #
    # Billing
    # ------------------------------------------------------------------ #
    def checkout(self, principal: Principal, plan: str, success_url: str, cancel_url: str) -> str:
        chosen = self._plan(plan, principal.tenant.kind)
        seats = len(self.store.members(principal.tenant.id)) if chosen.per_seat else 1
        email = principal.user.email if principal.user else None
        return self.billing.checkout_url(principal.tenant.id, plan, email, success_url, cancel_url, seats)

    def apply_billing_events(self, events: list[BillingEvent]) -> list[str]:
        """Apply provider events (idempotent per event id); returns the account ids changed."""
        changed: list[str] = []
        statuses = self.catalog.get("account_statuses") or {}
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
            if event.status and event.status in statuses:
                fields["status"] = event.status
            if fields:
                self.store.update_tenant(tenant.id, **fields)
                changed.append(tenant.id)
        return changed

    def sync_usage(self, period: str | None = None) -> list[dict[str, Any]]:
        """Report pay-to-use usage not yet reported to the billing provider (safe to re-run)."""
        period = period or current_period()
        sent: list[dict[str, Any]] = []
        for tenant in self.store.list_tenants():
            if not tenant.billing_customer_id:
                continue
            plan = self.plan_for(tenant)
            usage = self.store.get_usage(tenant.id, period)
            for metric, quantity in usage.items():
                rule = plan.rule(metric)
                if metric.startswith(REPORTED) or rule.overage != "bill":
                    continue
                # Only usage beyond the plan's allowance is billable.
                billable = rule.billable(quantity)
                delta = billable - usage.get(REPORTED + metric, 0.0)
                if delta <= 0:
                    continue
                identifier = f"{tenant.id}:{metric}:{period}:{billable:g}"
                if self.billing.report_usage(tenant.billing_customer_id, metric, delta, identifier):
                    self.store.add_usage(tenant.id, period, REPORTED + metric, delta)
                    sent.append({"account": tenant.id, "metric": metric, "quantity": delta})
        return sent

    # ------------------------------------------------------------------ #
    # Legal document acceptance
    # ------------------------------------------------------------------ #
    def get_legal_status(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Get legal acceptance status for a tenant (or default for signup)."""
        if not self.legal_enabled or not self.legal:
            return {"all_accepted": True, "documents": []}
        kind = "organization" if tenant_id else "individual"
        if tenant_id:
            tenant = self._tenant(tenant_id)
            kind = tenant.kind
        accepted = {}
        if tenant_id:
            tenant = self._tenant(tenant_id)
            accepted = tenant.metadata.get("legal_accepted", {}) if tenant.metadata else {}
        return self.legal.get_acceptance_status(kind, accepted)

    def accept_legal(self, tenant_id: str, accepted: dict[str, str]) -> dict[str, Any]:
        """Record legal document acceptance for a tenant."""
        if not self.legal_enabled or not self.legal:
            return {"all_accepted": True, "documents": []}
        tenant = self._tenant(tenant_id)
        metadata = dict(tenant.metadata or {})
        current = metadata.get("legal_accepted", {})
        current.update(accepted)
        metadata["legal_accepted"] = current
        self.store.update_tenant(tenant_id, metadata=metadata)
        return self.get_legal_status(tenant_id)

    def check_legal_acceptance(self, principal: Principal) -> None:
        """Raise if tenant hasn't accepted required legal documents."""
        if not self.legal_require_acceptance:
            return
        if not self.legal_enabled or not self.legal:
            return
        status = self.get_legal_status(principal.tenant.id)
        if not status["all_accepted"]:
            missing = [d["name"] for d in status["documents"] if not d["accepted"]]
            raise Forbidden(self.catalog.message("legal_not_accepted", documents=", ".join(missing)))

    def get_legal_document(self, key: str) -> LegalDocument | None:
        """Get the latest approved version of a legal document."""
        if not self.legal_enabled or not self.legal:
            return None
        return self.legal.get_approved(key)

    def list_legal_documents(self) -> list[dict[str, Any]]:
        """List all legal documents with their status."""
        if not self.legal_enabled or not self.legal:
            return []
        result = []
        for key in ["tos", "privacy", "dpa", "financial_disclaimer"]:
            doc = self.legal.get_approved(key)
            latest = self.legal.get_latest(key)
            result.append({
                "key": key,
                "name": doc.name if doc else (latest.name if latest else key),
                "required": key in self.legal.REQUIRED_KEYS or (key in self.legal.B2B_REQUIRED_KEYS),
                "has_approved": doc is not None,
                "approved_version": doc.version if doc else None,
                "latest_version": latest.version if latest else None,
            })
        return result

    # ------------------------------------------------------------------ #
    # Email
    # ------------------------------------------------------------------ #
    def send_email_template(self, to: str, template_key: str, variables: dict[str, Any],
                            from_email: str | None = None, from_name: str | None = None) -> bool:
        """Send an email using a template."""
        if not self.email_enabled or not self.email:
            raise PlatformError(self.catalog.message("email_disabled"))
        return self.email.send_template(to, template_key, variables, from_email, from_name)

    def send_verification_email(self, to: str, user_name: str, verification_url: str) -> bool:
        """Send email verification."""
        if not self.email_enabled or not self.email:
            return False
        return self.email.send_verification(to, user_name, verification_url)

    def send_password_reset_email(self, to: str, user_name: str, reset_url: str) -> bool:
        """Send password reset email."""
        if not self.email_enabled or not self.email:
            return False
        return self.email.send_password_reset(to, user_name, reset_url)

    def send_invite_email(self, to: str, account_name: str, inviter_name: str, role: str, invite_url: str) -> bool:
        """Send team invitation email."""
        if not self.email_enabled or not self.email:
            return False
        return self.email.send_invite(to, account_name, inviter_name, role, invite_url)

    def send_billing_receipt_email(self, to: str, amount: str, currency: str, plan_name: str,
                                    period: str, receipt_url: str) -> bool:
        """Send billing receipt email."""
        if not self.email_enabled or not self.email:
            return False
        return self.email.send_billing_receipt(to, amount, currency, plan_name, period, receipt_url)

    def send_usage_alert_email(self, to: str, account_name: str, metric: str, used: float,
                                limit: float, percent: float) -> bool:
        """Send usage alert email."""
        if not self.email_enabled or not self.email:
            return False
        return self.email.send_usage_alert(to, account_name, metric, used, limit, percent)

    def list_email_templates(self) -> list[dict[str, Any]]:
        """List all email templates."""
        if not self.email_enabled or not self.email:
            return []
        return [t.to_dict() for t in self.email.templates.get_all().values()]

    # ------------------------------------------------------------------ #
    # SSO
    # ------------------------------------------------------------------ #
    def get_sso_providers(self) -> list[dict[str, Any]]:
        """Get list of enabled SSO providers for login page."""
        if not self.sso_enabled or not self.sso:
            return []
        return self.sso.get_login_options()

    def initiate_sso_login(self, provider_key: str, redirect_uri: str) -> tuple[str, str]:
        """Start SSO login flow. Returns (auth_url, state)."""
        if not self.sso_enabled or not self.sso:
            raise PlatformError(self.catalog.message("sso_disabled"))
        return self.sso.initiate_login(provider_key, redirect_uri)

    def handle_sso_callback(self, code: str, state: str) -> tuple[str, str | None]:
        """Handle SSO callback. Returns (token, tenant_id)."""
        if not self.sso_enabled or not self.sso:
            raise PlatformError(self.catalog.message("sso_disabled"))
        return self.sso.handle_callback(code, state)

    def initiate_sso_logout(self, provider_key: str, redirect_uri: str | None = None) -> str | None:
        """Initiate SSO logout."""
        if not self.sso_enabled or not self.sso:
            return None
        return self.sso.initiate_logout(provider_key, redirect_uri)

    def list_sso_providers_admin(self) -> list[dict[str, Any]]:
        """List all SSO providers (admin view)."""
        if not self.sso_enabled or not self.sso:
            return []
        return [
            {
                "key": p.key,
                "name": p.name,
                "type": p.type,
                "enabled": p.enabled,
                "auto_provision": p.auto_provision,
                "default_role": p.default_role,
                "default_account_kind": p.default_account_kind,
                "default_plan": p.default_plan,
                "domain_hints": p.domain_hints,
            }
            for p in self.sso.store.get_all().values()
        ]

    def create_or_update_sso_provider(self, key: str, name: str, type_: str, config: dict[str, Any],
                                       enabled: bool = True, auto_provision: bool = True,
                                       default_role: str = "member", default_account_kind: str = "organization",
                                       default_plan: str = "free", domain_hints: list[str] | None = None) -> dict[str, Any]:
        """Create or update an SSO provider (admin)."""
        if not self.sso_enabled or not self.sso:
            raise PlatformError(self.catalog.message("sso_disabled"))
        provider = self.sso.store.create_or_update(
            key, name, type_, config, enabled, auto_provision,
            default_role, default_account_kind, default_plan, domain_hints
        )
        return provider.to_dict()

    def delete_sso_provider(self, key: str) -> bool:
        """Delete an SSO provider (admin)."""
        if not self.sso_enabled or not self.sso:
            raise PlatformError(self.catalog.message("sso_disabled"))
        return self.sso.store.delete(key)
