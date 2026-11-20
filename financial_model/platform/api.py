"""
HTTP surface of the commercial platform (FastAPI).

``install_platform`` does two things to the API app:

1. mounts the account endpoints under ``/v1`` (sign-up, login, organisations,
   members, API keys, usage, plans, billing checkout and webhook);
2. puts every model endpoint (``/calculate``, ``/dcf``, ``/lbo``, ``/ma``, ``/charts``,
   ``/export/excel``, ``/fetch``...) behind tenant authentication, plan entitlements
   and usage metering. A run is counted only when it succeeds.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from financial_model.models.registry import normalize_model_type
from financial_model.platform.billing import BillingError
from financial_model.platform.service import PlatformError, PlatformService, Principal

#: Endpoints that never need credentials in platform mode.
PLATFORM_PUBLIC = {"/v1/plans", "/v1/signup", "/v1/login", "/v1/billing/webhook"}


def _raise(exc: Exception) -> HTTPException:
    if isinstance(exc, PlatformError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    if isinstance(exc, BillingError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def _service(request: Request) -> PlatformService:
    service: PlatformService = request.app.state.platform
    return service


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
        raise _raise(exc) from exc
    request.state.principal = who
    return who


def _period_count(cfg: dict[str, Any]) -> int | None:
    periods = cfg.get("periods") or {}
    if not isinstance(periods, dict):
        return None
    if periods.get("count") is not None:
        return int(periods["count"])
    per_year = {"annual": 1, "quarterly": 4, "monthly": 12}.get(str(periods.get("type", "annual")), 1)
    return int(periods.get("years", 5)) * per_year


# path -> what the call needs from the plan
_GATES: dict[str, dict[str, Any]] = {
    "/calculate": {"export": "json"},
    "/dcf": {"model_type": "dcf", "export": "json"},
    "/lbo": {"model_type": "lbo", "export": "json"},
    "/ma": {"model_type": "ma", "export": "json"},
    "/charts": {"charts": True},
    "/export/excel": {"export": "excel"},
    "/fetch": {"data_fetch": True, "runs": 0, "metric": "fetches"},
    "/import": {"runs": 0, "metric": "imports"},
    "/synthetic": {"runs": 0, "metric": "synthetic"},
}


#: Model-router paths that stay open in platform mode (liveness probes).
OPEN_MODEL_PATHS = {"/health"}


async def metered(request: Request) -> AsyncIterator[Principal | None]:
    """Authenticate, check the plan before a model endpoint runs, record usage after it succeeds."""
    if request.url.path in OPEN_MODEL_PATHS:
        yield None
        return
    who = principal(request)
    service = _service(request)
    if request.method != "POST" or request.url.path not in _GATES:
        yield who  # read-only listings (/models, /providers): any authenticated caller
        return
    gate = dict(_GATES[request.url.path])
    metric = gate.pop("metric", "runs")
    body: Any = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - FastAPI reports malformed bodies itself
        body = {}
    cfg = body.get("config") if isinstance(body, dict) else None
    if isinstance(cfg, dict):
        if "model_type" not in gate:
            with contextlib.suppress(ValueError):  # the endpoint itself reports unknown types
                gate["model_type"] = normalize_model_type(cfg.get("model_type"))
        side = cfg.get("acquirer") if gate.get("model_type") == "ma" else cfg
        if isinstance(side, dict):
            gate["periods"] = _period_count(side)
    runs = int(gate.pop("runs", 1))
    try:
        service.check(who, runs=runs, **gate)
    except PlatformError as exc:
        raise _raise(exc) from exc
    yield who
    # Reached only when the endpoint returned without raising.
    service.record(who.tenant.id, metric, max(runs, 1))


# --------------------------------------------------------------------------- #
# /v1 account endpoints
# --------------------------------------------------------------------------- #
v1 = APIRouter(prefix="/v1", tags=["platform"])


class SignupRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8)
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_id: str | None = None


class OrganizationRequest(BaseModel):
    name: str
    plan: str | None = None


class MemberRequest(BaseModel):
    email: str
    role: str = "member"


class KeyRequest(BaseModel):
    name: str = "api"


class CheckoutRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str


def _tenant_dict(who: Principal) -> dict[str, Any]:
    t = who.tenant
    return {
        "id": t.id,
        "kind": t.kind,
        "name": t.name,
        "plan": who.plan.to_dict(),
        "status": t.status,
        "role": who.role,
    }


@v1.get("/plans")
async def plans(request: Request) -> dict[str, Any]:
    """Public plan catalogue (pricing page data), grouped by audience."""
    catalogue = [p.to_dict() for p in _service(request).plans.values()]
    return {
        "plans": catalogue,
        "individual": [p["key"] for p in catalogue if p["audience"] in ("individual", "any")],
        "organization": [p["key"] for p in catalogue if p["audience"] in ("organization", "any")],
    }


@v1.post("/signup", status_code=201)
async def signup(body: SignupRequest, request: Request) -> dict[str, Any]:
    """B2C self-serve sign-up: personal account on the default plan plus its first API key."""
    service = _service(request)
    try:
        user, tenant, key = service.signup_individual(body.email, body.password, body.name)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "tenant": {"id": tenant.id, "kind": tenant.kind, "plan": tenant.plan},
        "api_key": key,
        "note": "Store this API key now; it is not shown again.",
    }


@v1.post("/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    try:
        token, expires, tenant = _service(request).login(body.email, body.password, body.tenant_id)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {"token": token, "token_type": "bearer", "expires_at": expires, "tenant_id": tenant.id}


@v1.get("/me")
async def me(request: Request, who: Principal = Depends(principal)) -> dict[str, Any]:
    service = _service(request)
    accounts = (
        [
            {"id": t.id, "name": t.name, "kind": t.kind, "role": r}
            for t, r in service.store.tenants_for_user(who.user.id)
        ]
        if who.user
        else []
    )
    return {
        "user": {"id": who.user.id, "email": who.user.email, "name": who.user.name} if who.user else None,
        "tenant": _tenant_dict(who),
        "accounts": accounts,
        "usage": service.usage(who.tenant.id),
    }


@v1.post("/organizations", status_code=201)
async def create_organization(
    body: OrganizationRequest, request: Request, who: Principal = Depends(principal)
) -> dict[str, Any]:
    """B2B: create an organisation owned by the calling user (log in to it with /v1/login tenant_id)."""
    if who.user is None:
        raise HTTPException(status_code=400, detail="use a user's credentials, not a service key")
    try:
        tenant = _service(request).create_organization(body.name, who.user, body.plan)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {"id": tenant.id, "kind": tenant.kind, "name": tenant.name, "plan": tenant.plan}


@v1.get("/members")
async def list_members(request: Request, who: Principal = Depends(principal)) -> dict[str, Any]:
    members = _service(request).store.members(who.tenant.id)
    return {"members": [{"id": u.id, "email": u.email, "name": u.name, "role": r} for u, r in members]}


@v1.post("/members", status_code=201)
async def add_member(body: MemberRequest, request: Request, who: Principal = Depends(principal)) -> dict[str, Any]:
    try:
        who.require_role("admin")
        if body.role == "owner":
            who.require_role("owner")
        user = _service(request).add_member(who.tenant.id, body.email, body.role)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {"id": user.id, "email": user.email, "role": body.role, "password_set": user.password_hash is not None}


@v1.delete("/members/{user_id}")
async def remove_member(user_id: str, request: Request, who: Principal = Depends(principal)) -> dict[str, str]:
    try:
        who.require_role("admin")
        _service(request).remove_member(who.tenant.id, user_id)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {"removed": user_id}


@v1.get("/keys")
async def list_keys(request: Request, who: Principal = Depends(principal)) -> dict[str, Any]:
    keys = _service(request).store.list_keys(who.tenant.id)
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "prefix": f"fm_{k.prefix}_",
                "created_at": k.created_at,
                "last_used_at": k.last_used_at,
                "revoked": k.revoked_at is not None,
            }
            for k in keys
        ]
    }


@v1.post("/keys", status_code=201)
async def create_key(body: KeyRequest, request: Request, who: Principal = Depends(principal)) -> dict[str, Any]:
    try:
        who.require_role("admin" if who.tenant.kind == "organization" else "owner")
        plain, record = _service(request).issue_key(who.tenant.id, who.user.id if who.user else None, body.name)
    except PlatformError as exc:
        raise _raise(exc) from exc
    return {
        "id": record.id,
        "name": record.name,
        "api_key": plain,
        "note": "Store this key now; it is not shown again.",
    }


@v1.delete("/keys/{key_id}")
async def revoke_key(key_id: str, request: Request, who: Principal = Depends(principal)) -> dict[str, str]:
    try:
        who.require_role("admin" if who.tenant.kind == "organization" else "owner")
    except PlatformError as exc:
        raise _raise(exc) from exc
    if not _service(request).store.revoke_key(who.tenant.id, key_id):
        raise HTTPException(status_code=404, detail="no such active key")
    return {"revoked": key_id}


@v1.get("/usage")
async def usage(request: Request, period: str | None = None, who: Principal = Depends(principal)) -> dict[str, Any]:
    return _service(request).usage(who.tenant.id, period)


@v1.post("/billing/checkout")
async def checkout(body: CheckoutRequest, request: Request, who: Principal = Depends(principal)) -> dict[str, str]:
    """Start a paid subscription: returns the payment provider's hosted checkout URL."""
    try:
        url = _service(request).checkout(who, body.plan, body.success_url, body.cancel_url)
    except (PlatformError, BillingError) as exc:
        raise _raise(exc) from exc
    return {"url": url}


@v1.post("/billing/webhook")
async def billing_webhook(request: Request) -> dict[str, Any]:
    """Payment-provider webhook (signature-verified by the provider class)."""
    service = _service(request)
    payload = await request.body()
    try:
        events = service.billing.parse_webhook(payload, dict(request.headers))
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"received": len(events), "updated": service.apply_billing_events(events)}


def install_platform(application: FastAPI, model_router: APIRouter, service: PlatformService) -> None:
    """Mount /v1 and the model endpoints behind tenant auth, entitlements and metering."""
    application.state.platform = service
    application.include_router(v1)
    application.include_router(model_router, dependencies=[Depends(metered)])
