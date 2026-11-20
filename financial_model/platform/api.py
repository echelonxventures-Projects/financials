"""
HTTP surface of the commercial platform (FastAPI).

``install_platform`` mounts the account endpoints under ``/v1`` and puts **every**
route (model endpoints and ``/v1``) behind one guard driven by the catalogue's
``endpoints`` table: whether the route is public, which role permission it needs,
which plan features, and which metric it meters (checked before the call, recorded
only when the call succeeds). Changing who may call what, or what it costs, is a
catalogue change; no route rule is written in code.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from financial_model.models.registry import normalize_model_type
from financial_model.platform.billing import BillingError
from financial_model.platform.service import PlatformError, PlatformService, Principal


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, PlatformError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _service(request: Request) -> PlatformService:
    service: PlatformService = request.app.state.platform
    return service


def _route_key(request: Request) -> tuple[str, str]:
    route = request.scope.get("route")
    return request.method, str(getattr(route, "path", request.url.path))


def principal(request: Request) -> Principal:
    """Authenticate the caller from ``X-API-Key`` or ``Authorization: Bearer``."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    try:
        who = _service(request).authenticate(api_key=request.headers.get("x-api-key") or None, bearer=bearer)
    except PlatformError as exc:
        raise _http(exc) from exc
    request.state.principal = who
    return who


def _period_count(cfg: dict[str, Any]) -> int | None:
    from financial_model.core.period import PeriodType

    periods = cfg.get("periods") or {}
    if not isinstance(periods, dict):
        return None
    if periods.get("count") is not None:
        return int(periods["count"])
    try:
        per_year = PeriodType(str(periods.get("type", PeriodType.ANNUAL.value))).periods_per_year
        return int(periods.get("years", 1)) * per_year
    except (ValueError, TypeError):
        return None  # the endpoint reports the bad config itself


async def guard(request: Request) -> AsyncIterator[Principal | None]:
    """Apply the catalogue rule for this route; record metered usage after success."""
    service = _service(request)
    spec = service.catalog.endpoint(*_route_key(request))
    if spec.get("public"):
        yield None
        return
    who = principal(request)
    metric = spec.get("metric")
    check: dict[str, Any] = {
        "permission": spec.get("permission"),
        "features": list(spec.get("features") or ()),
        "metric": metric,
        "quantity": float(spec.get("quantity", 1)),
        "model_type": spec.get("model_type"),
    }
    if metric:
        body: Any = {}
        with contextlib.suppress(Exception):  # malformed bodies are reported by the endpoint
            body = await request.json()
        cfg = body.get("config") if isinstance(body, dict) else None
        if isinstance(cfg, dict):
            if not check["model_type"]:
                with contextlib.suppress(ValueError):  # unknown types are reported by the endpoint
                    check["model_type"] = normalize_model_type(cfg.get("model_type"))
            source = cfg.get(spec["periods_from"]) if spec.get("periods_from") else cfg
            if isinstance(source, dict):
                check["periods"] = _period_count(source)
    try:
        service.check(who, **check)
    except PlatformError as exc:
        raise _http(exc) from exc
    yield who
    if metric:  # reached only when the endpoint returned without raising
        service.record(who.tenant.id, metric, check["quantity"])


# --------------------------------------------------------------------------- #
# /v1 account endpoints (permissions: catalogue `endpoints`)
# --------------------------------------------------------------------------- #
v1 = APIRouter(prefix="/v1", tags=["platform"])


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_id: str | None = None


class AccountRequest(BaseModel):
    name: str
    kind: str | None = None
    plan: str | None = None


class MemberRequest(BaseModel):
    email: str
    role: str | None = None


class KeyRequest(BaseModel):
    name: str = "api"


class CheckoutRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except (PlatformError, BillingError, ValueError) as exc:
        raise _http(exc) from exc


@v1.get("/plans")
async def plans(request: Request) -> dict[str, Any]:
    """Public catalogue for a pricing page: plans by account kind, metrics, currency."""
    catalog = _service(request).catalog
    kinds = catalog.get("account_kinds") or {}
    return {
        "currency": catalog.currency,
        "plans": [p.to_dict() for p in catalog.plans.values()],
        "by_account_kind": {k: [p.key for p in catalog.plans.values() if p.available_to(k)] for k in kinds},
        "metrics": catalog.get("metrics") or {},
        "account_kinds": kinds,
    }


@v1.post("/signup", status_code=201)
async def signup(body: SignupRequest, request: Request) -> dict[str, Any]:
    """Self-serve sign-up (the catalogue's ``signup_kind``; B2C by default) plus a first API key."""
    user, tenant, key = _call(_service(request).signup, body.email, body.password, body.name)
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "tenant": {"id": tenant.id, "kind": tenant.kind, "plan": tenant.plan},
        "api_key": key,
        "note": "Store this API key now; it is not shown again.",
    }


@v1.post("/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    token, expires, tenant = _call(_service(request).login, body.email, body.password, body.tenant_id)
    return {"token": token, "token_type": "bearer", "expires_at": expires, "tenant_id": tenant.id}


@v1.get("/me")
async def me(request: Request) -> dict[str, Any]:
    service, who = _service(request), principal(request)
    accounts = (
        [
            {"id": t.id, "name": t.name, "kind": t.kind, "role": r}
            for t, r in service.store.tenants_for_user(who.user.id)
        ]
        if who.user
        else []
    )
    t = who.tenant
    return {
        "user": {"id": who.user.id, "email": who.user.email, "name": who.user.name} if who.user else None,
        "tenant": {"id": t.id, "kind": t.kind, "name": t.name, "status": t.status, "role": who.role},
        "plan": who.plan.to_dict(),
        "accounts": accounts,
        "usage": service.usage(t.id),
    }


@v1.post("/accounts", status_code=201)
async def create_account(body: AccountRequest, request: Request) -> dict[str, Any]:
    """Create an account owned by the calling user (B2B organisation by default)."""
    who = principal(request)
    if who.user is None:
        raise HTTPException(status_code=400, detail="use a user's credentials, not a service key")
    tenant = _call(_service(request).create_user_account, who.user, body.name, body.kind, body.plan)
    return {"id": tenant.id, "kind": tenant.kind, "name": tenant.name, "plan": tenant.plan}


@v1.get("/members")
async def list_members(request: Request) -> dict[str, Any]:
    members = _service(request).store.members(principal(request).tenant.id)
    return {"members": [{"id": u.id, "email": u.email, "name": u.name, "role": r} for u, r in members]}


@v1.post("/members", status_code=201)
async def add_member(body: MemberRequest, request: Request) -> dict[str, Any]:
    service, who = _service(request), principal(request)
    role = body.role or str(service.catalog.get("default_member_role"))
    if _call(service.catalog.rank, role) > service.catalog.rank(who.role):
        raise HTTPException(status_code=403, detail=f"you cannot grant a role above your own ({who.role})")
    user = _call(service.add_member, who.tenant.id, body.email, role)
    return {"id": user.id, "email": user.email, "role": role, "password_set": user.password_hash is not None}


@v1.delete("/members/{user_id}")
async def remove_member(user_id: str, request: Request) -> dict[str, str]:
    _call(_service(request).remove_member, principal(request).tenant.id, user_id)
    return {"removed": user_id}


@v1.get("/keys")
async def list_keys(request: Request) -> dict[str, Any]:
    service = _service(request)
    keys = service.store.list_keys(principal(request).tenant.id)
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "prefix": f"{service.keys.brand}_{k.prefix}_",
                "created_at": k.created_at,
                "last_used_at": k.last_used_at,
                "revoked": k.revoked_at is not None,
            }
            for k in keys
        ]
    }


@v1.post("/keys", status_code=201)
async def create_key(body: KeyRequest, request: Request) -> dict[str, Any]:
    who = principal(request)
    plain, record = _call(_service(request).issue_key, who.tenant.id, who.user.id if who.user else None, body.name)
    return {
        "id": record.id,
        "name": record.name,
        "api_key": plain,
        "note": "Store this key now; it is not shown again.",
    }


@v1.delete("/keys/{key_id}")
async def revoke_key(key_id: str, request: Request) -> dict[str, str]:
    if not _service(request).store.revoke_key(principal(request).tenant.id, key_id):
        raise HTTPException(status_code=404, detail="no such active key")
    return {"revoked": key_id}


@v1.get("/usage")
async def usage(request: Request, period: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _call(_service(request).usage, principal(request).tenant.id, period)
    return result


@v1.get("/invoice")
async def invoice(request: Request, period: str | None = None) -> dict[str, Any]:
    """Charges so far this month (or ``period``): plan fee, seats and pay-to-use usage."""
    result: dict[str, Any] = _call(_service(request).invoice, principal(request).tenant.id, period)
    return result


@v1.post("/billing/checkout")
async def checkout(body: CheckoutRequest, request: Request) -> dict[str, str]:
    """Start a paid subscription: returns the payment provider's hosted checkout URL."""
    url = _call(_service(request).checkout, principal(request), body.plan, body.success_url, body.cancel_url)
    return {"url": url}


@v1.post("/billing/webhook")
async def billing_webhook(request: Request) -> dict[str, Any]:
    """Payment-provider webhook (signature-verified by the provider class)."""
    service = _service(request)
    events = _call(service.billing.parse_webhook, await request.body(), dict(request.headers))
    return {"received": len(events), "updated": service.apply_billing_events(events)}


def install_platform(application: FastAPI, model_router: APIRouter, service: PlatformService) -> None:
    """Mount /v1 and put every route behind the catalogue-driven guard."""
    application.state.platform = service
    application.include_router(v1, dependencies=[Depends(guard)])
    application.include_router(model_router, dependencies=[Depends(guard)])
