"""
Financial Model Admin Portal
A complete administration interface for managing platform configuration via Admin API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from financial_model.platform.api import _service
from financial_model.platform.service import PlatformError, PlatformService, Principal

# Admin router
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# Templates and static files
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ============================================
# Authentication
# ============================================

async def get_admin_principal(request: Request) -> Principal:
    """Get authenticated principal with admin permissions."""
    service = _service(request)
    # Use platform authentication but require manage_billing permission
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    api_key = request.headers.get("x-api-key")

    try:
        principal = service.authenticate(api_key=api_key, bearer=bearer)
    except PlatformError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # Check admin permission
    if not principal.can("manage_billing"):
        raise HTTPException(status_code=403, detail="Admin access required")

    return principal


def get_admin_service(request: Request) -> PlatformService:
    """Get platform service instance."""
    return _service(request)


# ============================================
# Page Routes (Server-side rendered)
# ============================================

@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Admin login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@admin_router.post("/login")
async def login(request: Request):
    """Handle admin login."""
    form = await request.form()
    email = form.get("email")
    password = form.get("password")

    service = get_admin_service(request)
    try:
        token, expires, tenant = service.login(email, password)
        # Check if user has admin permissions
        user = service.store.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Check membership with admin role
        memberships = service.store.tenants_for_user(user.id)
        is_admin = any(role in ("owner", "admin") for _, role in memberships)

        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")

        # Return token for frontend storage
        return {"token": token, "expires_at": expires, "tenant_id": tenant.id}
    except PlatformError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@admin_router.get("/pages/{page}", response_class=HTMLResponse)
async def admin_page(page: str, request: Request, principal: Principal = Depends(get_admin_principal)):
    """Serve admin page partials."""
    # Map page names to template files
    page_templates = {
        "dashboard": "pages/dashboard.html",
        "legal": "pages/legal.html",
        "email": "pages/email.html",
        "sso": "pages/sso.html",
        "plans": "pages/plans.html",
        "billing": "pages/billing.html",
        "roles": "pages/roles.html",
        "account-kinds": "pages/account_kinds.html",
        "metrics": "pages/metrics.html",
        "endpoints": "pages/endpoints.html",
        "messages": "pages/messages.html",
        "settings": "pages/settings.html",
        "currency": "pages/currency.html",
        "account-statuses": "pages/account_statuses.html",
        "default-plans": "pages/default_plans.html",
        "special-roles": "pages/special_roles.html",
        "default-metering": "pages/default_metering.html",
        "infrastructure": "pages/infrastructure.html",
    }

    template_name = page_templates.get(page)
    if not template_name:
        raise HTTPException(status_code=404, detail="Page not found")

    # Get service for page data
    service = get_admin_service(request)

    # Build context based on page
    context = {"request": request, "principal": principal, "page": page}

    if page == "dashboard":
        context.update(await get_dashboard_context(service, principal))
    elif page == "legal":
        context.update(await get_legal_context(service))
    elif page == "email":
        context.update(await get_email_context(service))
    elif page == "sso":
        context.update(await get_sso_context(service))
    elif page == "plans":
        context.update(await get_plans_context(service))
    elif page == "billing":
        context.update(await get_billing_context(service))
    elif page == "roles":
        context.update(await get_roles_context(service))
    elif page == "account-kinds":
        context.update(await get_account_kinds_context(service))
    elif page == "metrics":
        context.update(await get_metrics_context(service))
    elif page == "endpoints":
        context.update(await get_endpoints_context(service))
    elif page == "messages":
        context.update(await get_messages_context(service))
    elif page == "settings":
        context.update(await get_settings_context(service))
    elif page == "currency":
        context.update(await get_currency_context(service))
    elif page == "account-statuses":
        context.update(await get_account_statuses_context(service))
    elif page == "default-plans":
        context.update(await get_default_plans_context(service))
    elif page == "special-roles":
        context.update(await get_special_roles_context(service))
    elif page == "default-metering":
        context.update(await get_default_metering_context(service))
    elif page == "infrastructure":
        context.update(await get_infrastructure_context(service))

    return templates.TemplateResponse(template_name, context)


# ============================================
# API Endpoints for Admin Operations
# ============================================

# Legal Documents API
@admin_router.get("/api/legal")
async def list_legal_documents(service: PlatformService = Depends(get_admin_service)):
    """List all legal documents."""
    if not service.legal_enabled or not service.legal:
        return {"documents": []}
    return {"documents": service.list_legal_documents()}


@admin_router.get("/api/legal/{key}")
async def get_legal_document(key: str, service: PlatformService = Depends(get_admin_service)):
    """Get a legal document by key."""
    if not service.legal_enabled or not service.legal:
        raise HTTPException(status_code=404, detail="Legal documents not enabled")

    doc = service.get_legal_document(key)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc.to_dict()


@admin_router.post("/api/legal")
async def create_legal_document(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create or update a legal document."""
    if not service.legal_enabled or not service.legal:
        raise HTTPException(status_code=400, detail="Legal documents not enabled")

    data = await request.json()
    key = data.get("key")
    name = data.get("name")
    content = data.get("content")
    required = data.get("required", True)
    version = data.get("version")
    metadata = data.get("metadata", {})
    auto_approve = data.get("auto_approve", False)

    if not key or not name or not content:
        raise HTTPException(status_code=400, detail="key, name, and content are required")

    # Validate content
    errors = service.validate_legal_content(content, key)
    if any("Content cannot be empty" in e for e in errors):
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    doc = service.legal.create_version(
        key=key,
        content=content,
        version=version,
        required=required,
        metadata=metadata,
        operator_id=principal.user.id if principal.user else "admin",
        auto_approve=auto_approve,
    )

    return doc.to_dict()


@admin_router.post("/api/legal/{key}/approve")
async def approve_legal_document(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Approve a specific version of a legal document."""
    if not service.legal_enabled or not service.legal:
        raise HTTPException(status_code=400, detail="Legal documents not enabled")

    data = await request.json()
    version = data.get("version")
    if not version:
        raise HTTPException(status_code=400, detail="version is required")

    # Get operator ID from principal (need to pass principal)
    # For now use a placeholder
    doc = service.legal.approve_version(key, version, "admin")
    if not doc:
        raise HTTPException(status_code=404, detail="Version not found")

    return doc.to_dict()


@admin_router.delete("/api/legal/{key}")
async def delete_legal_document(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Delete a legal document (all versions)."""
    if not service.legal_enabled or not service.legal:
        raise HTTPException(status_code=400, detail="Legal documents not enabled")

    # This would need a delete method on LegalStore
    raise HTTPException(status_code=501, detail="Delete not implemented")


# Email Templates API
@admin_router.get("/api/email/templates")
async def list_email_templates(service: PlatformService = Depends(get_admin_service)):
    """List all email templates."""
    if not service.email_enabled or not service.email:
        return {"templates": []}
    return {"templates": service.list_email_templates()}


@admin_router.get("/api/email/templates/{key}")
async def get_email_template(key: str, service: PlatformService = Depends(get_admin_service)):
    """Get an email template by key."""
    if not service.email_enabled or not service.email:
        raise HTTPException(status_code=404, detail="Email not enabled")

    template = service.email.templates.get(key)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


@admin_router.post("/api/email/templates")
async def create_email_template(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create or update an email template."""
    if not service.email_enabled or not service.email:
        raise HTTPException(status_code=400, detail="Email not enabled")

    data = await request.json()
    key = data.get("key")
    name = data.get("name")
    subject = data.get("subject")
    text_body = data.get("text_body")
    html_body = data.get("html_body")
    required_vars = data.get("required_vars", [])

    if not all([key, name, subject, text_body]):
        raise HTTPException(status_code=400, detail="key, name, subject, and text_body are required")

    template = service.email.templates.create_or_update(
        key=key,
        name=name,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        required_vars=required_vars,
    )

    return template.to_dict()


@admin_router.delete("/api/email/templates/{key}")
async def delete_email_template(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete an email template."""
    if not service.email_enabled or not service.email:
        raise HTTPException(status_code=400, detail="Email not enabled")

    # Check if built-in
    builtin_keys = {"verify_email", "password_reset", "invite_member", "billing_receipt", "usage_alert"}
    if key in builtin_keys:
        raise HTTPException(status_code=400, detail="Cannot delete built-in template")

    deleted = service.email.templates.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")

    return {"deleted": True}


@admin_router.post("/api/email/test")
async def send_test_email(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Send a test email."""
    if not service.email_enabled or not service.email:
        raise HTTPException(status_code=400, detail="Email not enabled")

    data = await request.json()
    to = data.get("to")
    template_key = data.get("template", "verify_email")
    variables = data.get("variables", {})

    if not to:
        raise HTTPException(status_code=400, detail="to is required")

    success = service.send_email_template(
        to=to,
        template_key=template_key,
        variables=variables,
    )

    return {"sent": success}


# SSO Providers API
@admin_router.get("/api/sso/providers")
async def list_sso_providers(service: PlatformService = Depends(get_admin_service)):
    """List all SSO providers (admin view)."""
    if not service.sso_enabled or not service.sso:
        return {"providers": []}
    return {"providers": service.list_sso_providers_admin()}


@admin_router.get("/api/sso/providers/{key}")
async def get_sso_provider(key: str, service: PlatformService = Depends(get_admin_service)):
    """Get an SSO provider by key."""
    if not service.sso_enabled or not service.sso:
        raise HTTPException(status_code=404, detail="SSO not enabled")

    provider = service.sso.store.get(key)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider.to_dict()


@admin_router.post("/api/sso/providers")
async def create_sso_provider(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create or update an SSO provider."""
    if not service.sso_enabled or not service.sso:
        raise HTTPException(status_code=400, detail="SSO not enabled")

    data = await request.json()
    key = data.get("key")
    name = data.get("name")
    type_ = data.get("type")
    config = data.get("config", {})
    enabled = data.get("enabled", True)
    auto_provision = data.get("auto_provision", True)
    default_role = data.get("default_role", "member")
    default_account_kind = data.get("default_account_kind", "organization")
    default_plan = data.get("default_plan", "free")
    domain_hints = data.get("domain_hints", [])

    if not all([key, name, type_]):
        raise HTTPException(status_code=400, detail="key, name, and type are required")

    result = service.create_or_update_sso_provider(
        key=key,
        name=name,
        type=type_,
        config=config,
        enabled=enabled,
        auto_provision=auto_provision,
        default_role=default_role,
        default_account_kind=default_account_kind,
        default_plan=default_plan,
        domain_hints=domain_hints,
    )

    return result


@admin_router.delete("/api/sso/providers/{key}")
async def delete_sso_provider(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete an SSO provider."""
    if not service.sso_enabled or not service.sso:
        raise HTTPException(status_code=400, detail="SSO not enabled")

    deleted = service.delete_sso_provider(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")

    return {"deleted": True}


# Plans & Pricing API
@admin_router.get("/api/plans")
async def list_plans(service: PlatformService = Depends(get_admin_service)):
    """List all plans."""
    return {"plans": [p.to_dict() for p in service.catalog.plans.values()], "currency": service.catalog.currency}


@admin_router.post("/api/plans")
async def create_plan(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new plan."""
    data = await request.json()
    key = data.get("key")
    name = data.get("name")
    if not key or not name:
        raise HTTPException(status_code=400, detail="key and name are required")
    if key in service.catalog.plans:
        raise HTTPException(status_code=409, detail="Plan already exists")
    # Store in catalog (will be persisted via catalog file)
    plan_data = {
        "name": name,
        "description": data.get("description", ""),
        "price_monthly": data.get("price_monthly", 0),
        "price_yearly": data.get("price_yearly", 0),
        "audience": data.get("audience", "both"),
        "features": data.get("features", []),
        "limits": data.get("limits", {}),
        "metering": data.get("metering", {}),
    }
    service.catalog._data["plans"][key] = plan_data
    service.catalog.save()
    return {"created": True, "key": key}


@admin_router.put("/api/plans/{key}")
async def update_plan(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update a plan."""
    if key not in service.catalog.plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    data = await request.json()
    plan_data = service.catalog._data["plans"][key]
    for field in ["name", "description", "price_monthly", "price_yearly", "audience", "features", "limits", "metering"]:
        if field in data:
            plan_data[field] = data[field]
    service.catalog.save()
    return {"updated": True, "key": key}


@admin_router.delete("/api/plans/{key}")
async def delete_plan(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete a plan."""
    if key not in service.catalog.plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    # Check if plan is in use
    for tenant in service.store.tenants.values():
        if tenant.plan == key:
            raise HTTPException(status_code=400, detail=f"Plan in use by tenant {tenant.name}")
    del service.catalog._data["plans"][key]
    service.catalog.save()
    return {"deleted": True}


# Roles & Permissions API
@admin_router.get("/api/roles")
async def list_roles(service: PlatformService = Depends(get_admin_service)):
    """List all roles."""
    roles = service.catalog.get("roles") or {}
    return {"roles": {k: v for k, v in roles.items()}}


@admin_router.post("/api/roles")
async def create_role(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new role."""
    data = await request.json()
    key = data.get("key")
    label = data.get("label")
    rank = data.get("rank", 10)
    permissions = data.get("permissions", [])
    if not key or not label:
        raise HTTPException(status_code=400, detail="key and label are required")
    if key in (service.catalog.get("roles") or {}):
        raise HTTPException(status_code=409, detail="Role already exists")
    roles = service.catalog._data.setdefault("roles", {})
    roles[key] = {"label": label, "rank": rank, "permissions": permissions}
    service.catalog.save()
    return {"created": True, "key": key}


@admin_router.put("/api/roles/{key}")
async def update_role(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update a role."""
    roles = service.catalog.get("roles") or {}
    if key not in roles:
        raise HTTPException(status_code=404, detail="Role not found")
    data = await request.json()
    for field in ["label", "rank", "permissions"]:
        if field in data:
            roles[key][field] = data[field]
    service.catalog.save()
    return {"updated": True, "key": key}


@admin_router.delete("/api/roles/{key}")
async def delete_role(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete a role."""
    roles = service.catalog.get("roles") or {}
    if key not in roles:
        raise HTTPException(status_code=404, detail="Role not found")
    # Prevent deletion of special roles
    if key in (service.catalog.get("default_member_role"), service.catalog.get("creator_role"), service.catalog.get("service_key_role")):
        raise HTTPException(status_code=400, detail="Cannot delete special role")
    del roles[key]
    service.catalog.save()
    return {"deleted": True}


# Account Kinds API
@admin_router.get("/api/account-kinds")
async def list_account_kinds(service: PlatformService = Depends(get_admin_service)):
    """List all account kinds."""
    kinds = service.catalog.get("account_kinds") or {}
    return {
        "account_kinds": kinds,
        "signup_kind": service.catalog.get("signup_kind"),
        "default_account_kind": service.catalog.get("default_account_kind"),
    }


@admin_router.post("/api/account-kinds")
async def create_account_kind(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new account kind."""
    data = await request.json()
    key = data.get("key")
    label = data.get("label")
    if not key or not label:
        raise HTTPException(status_code=400, detail="key and label are required")
    kinds = service.catalog._data.setdefault("account_kinds", {})
    if key in kinds:
        raise HTTPException(status_code=409, detail="Account kind already exists")
    kinds[key] = {
        "label": label,
        "max_members": data.get("max_members"),
        "signup": data.get("signup", False),
        "user_creatable": data.get("user_creatable", False),
    }
    service.catalog.save()
    return {"created": True, "key": key}


@admin_router.put("/api/account-kinds/{key}")
async def update_account_kind(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update an account kind."""
    kinds = service.catalog.get("account_kinds") or {}
    if key not in kinds:
        raise HTTPException(status_code=404, detail="Account kind not found")
    data = await request.json()
    for field in ["label", "max_members", "signup", "user_creatable"]:
        if field in data:
            kinds[key][field] = data[field]
    service.catalog.save()
    return {"updated": True, "key": key}


@admin_router.delete("/api/account-kinds/{key}")
async def delete_account_kind(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete an account kind."""
    kinds = service.catalog.get("account_kinds") or {}
    if key not in kinds:
        raise HTTPException(status_code=404, detail="Account kind not found")
    if key == service.catalog.get("signup_kind") or key == service.catalog.get("default_account_kind"):
        raise HTTPException(status_code=400, detail="Cannot delete default/signup account kind")
    del kinds[key]
    service.catalog.save()
    return {"deleted": True}


# Metrics & Metering API
@admin_router.get("/api/metrics")
async def list_metrics(service: PlatformService = Depends(get_admin_service)):
    """List all metrics."""
    metrics = service.catalog.get("metrics") or {}
    default_metering = service.catalog.get("default_metering") or {}
    result = {}
    for key, metric in metrics.items():
        result[key] = {
            **metric,
            "default_included": default_metering.get(key, {}).get("included"),
            "default_overage": default_metering.get(key, {}).get("overage"),
        }
    return {"metrics": result}


@admin_router.post("/api/metrics")
async def create_metric(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new metric."""
    data = await request.json()
    key = data.get("key")
    name = data.get("name")
    unit = data.get("unit", "units")
    if not key or not name:
        raise HTTPException(status_code=400, detail="key and name are required")
    metrics = service.catalog._data.setdefault("metrics", {})
    if key in metrics:
        raise HTTPException(status_code=409, detail="Metric already exists")
    metrics[key] = {
        "name": name,
        "unit": unit,
        "description": data.get("description", ""),
    }
    default_metering = service.catalog._data.setdefault("default_metering", {})
    if "default_included" in data or "default_overage" in data:
        default_metering[key] = {
            "included": data.get("default_included"),
            "overage": data.get("default_overage"),
        }
    service.catalog.save()
    return {"created": True, "key": key}


@admin_router.put("/api/metrics/{key}")
async def update_metric(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update a metric."""
    metrics = service.catalog.get("metrics") or {}
    if key not in metrics:
        raise HTTPException(status_code=404, detail="Metric not found")
    data = await request.json()
    for field in ["name", "unit", "description"]:
        if field in data:
            metrics[key][field] = data[field]
    default_metering = service.catalog._data.setdefault("default_metering", {})
    if "default_included" in data or "default_overage" in data:
        default_metering[key] = {
            "included": data.get("default_included", default_metering.get(key, {}).get("included")),
            "overage": data.get("default_overage", default_metering.get(key, {}).get("overage")),
        }
    service.catalog.save()
    return {"updated": True, "key": key}


@admin_router.delete("/api/metrics/{key}")
async def delete_metric(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete a metric."""
    metrics = service.catalog.get("metrics") or {}
    if key not in metrics:
        raise HTTPException(status_code=404, detail="Metric not found")
    # Check if metric is used in any plan's metering
    for plan in service.catalog.plans.values():
        if key in plan.metering:
            raise HTTPException(status_code=400, detail=f"Metric in use by plan {plan.key}")
    del metrics[key]
    default_metering = service.catalog.get("default_metering") or {}
    if key in default_metering:
        del default_metering[key]
    service.catalog.save()
    return {"deleted": True}


@admin_router.get("/api/metering")
async def list_metering_rules(service: PlatformService = Depends(get_admin_service)):
    """List metering rules for all plans."""
    result = {}
    for plan_key, plan in service.catalog.plans.items():
        result[plan_key] = {k: v.to_dict() for k, v in plan.metering.items()}
    return {"metering": result}


@admin_router.put("/api/metering/{plan_key}/{metric_key}")
async def update_metering_rule(plan_key: str, metric_key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update metering rule for a plan."""
    if plan_key not in service.catalog.plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    data = await request.json()
    plan = service.catalog.plans[plan_key]
    if metric_key not in plan.metering:
        raise HTTPException(status_code=404, detail="Metering rule not found")
    metering = plan.metering[metric_key]
    if "included" in data:
        metering.included = data["included"]
    if "overage" in data:
        metering.overage = data["overage"]
    # Save via catalog
    plan_data = service.catalog._data["plans"][plan_key]
    plan_data.setdefault("metering", {})[metric_key] = {
        "included": metering.included,
        "overage": metering.overage,
    }
    service.catalog.save()
    return {"updated": True}


# Endpoints API
@admin_router.get("/api/endpoints")
async def list_endpoints(service: PlatformService = Depends(get_admin_service)):
    """List all endpoint rules."""
    endpoints = service.catalog.get("endpoints") or {}
    default_endpoint = service.catalog.get("default_endpoint") or {}
    return {"endpoints": endpoints, "default_endpoint": default_endpoint}


@admin_router.post("/api/endpoints")
async def create_endpoint(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new endpoint rule."""
    data = await request.json()
    pattern = data.get("pattern")
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    endpoints = service.catalog._data.setdefault("endpoints", {})
    if pattern in endpoints:
        raise HTTPException(status_code=409, detail="Endpoint pattern already exists")
    endpoints[pattern] = {
        "permission": data.get("permission"),
        "features": data.get("features", []),
        "metric": data.get("metric"),
        "quantity": data.get("quantity", 1),
        "model_type": data.get("model_type"),
        "periods_from": data.get("periods_from"),
        "public": data.get("public", False),
    }
    service.catalog.save()
    return {"created": True, "pattern": pattern}


@admin_router.put("/api/endpoints/{pattern:path}")
async def update_endpoint(pattern: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update an endpoint rule."""
    endpoints = service.catalog.get("endpoints") or {}
    if pattern not in endpoints:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    data = await request.json()
    for field in ["permission", "features", "metric", "quantity", "model_type", "periods_from", "public"]:
        if field in data:
            endpoints[pattern][field] = data[field]
    service.catalog.save()
    return {"updated": True, "pattern": pattern}


@admin_router.delete("/api/endpoints/{pattern:path}")
async def delete_endpoint(pattern: str, service: PlatformService = Depends(get_admin_service)):
    """Delete an endpoint rule."""
    endpoints = service.catalog.get("endpoints") or {}
    if pattern not in endpoints:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    del endpoints[pattern]
    service.catalog.save()
    return {"deleted": True}


@admin_router.put("/api/endpoints/default")
async def update_default_endpoint(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update default endpoint configuration."""
    data = await request.json()
    default_endpoint = service.catalog._data.setdefault("default_endpoint", {})
    for field in ["permission", "features", "metric", "quantity", "model_type", "periods_from", "public"]:
        if field in data:
            default_endpoint[field] = data[field]
    service.catalog.save()
    return {"updated": True}


# Messages API
@admin_router.get("/api/messages")
async def list_messages(service: PlatformService = Depends(get_admin_service)):
    """List all messages."""
    messages = service.catalog.get("messages") or {}
    return {"messages": messages}


@admin_router.post("/api/messages")
async def create_message(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Create a new message."""
    data = await request.json()
    key = data.get("key")
    message = data.get("message")
    if not key or not message:
        raise HTTPException(status_code=400, detail="key and message are required")
    messages = service.catalog._data.setdefault("messages", {})
    if key in messages:
        raise HTTPException(status_code=409, detail="Message already exists")
    messages[key] = message
    service.catalog.save()
    return {"created": True, "key": key}


@admin_router.put("/api/messages/{key}")
async def update_message(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update a message."""
    messages = service.catalog.get("messages") or {}
    if key not in messages:
        raise HTTPException(status_code=404, detail="Message not found")
    data = await request.json()
    if "message" in data:
        messages[key] = data["message"]
    service.catalog.save()
    return {"updated": True, "key": key}


@admin_router.delete("/api/messages/{key}")
async def delete_message(key: str, service: PlatformService = Depends(get_admin_service)):
    """Delete a message."""
    messages = service.catalog.get("messages") or {}
    if key not in messages:
        raise HTTPException(status_code=404, detail="Message not found")
    del messages[key]
    service.catalog.save()
    return {"deleted": True}


# Settings API
@admin_router.get("/api/settings")
async def get_settings(service: PlatformService = Depends(get_admin_service)):
    """Get platform settings (runtime only)."""
    import sys
    return {
        "secret_key_set": bool(service._configured_secret),
        "token_ttl_hours": service.token_ttl / 3600,
        "password_iterations": service.password_iterations,
        "password_min_length": service.password_min_length,
        "api_key_prefix": service.keys.brand,
        "allow_signup": service.allow_signup,
        "require_secret_key": service.require_secret_key,
        "secret_key_min_length": service.secret_key_min_length,
        "login_throttle": {
            "window_seconds": service.login_window,
            "max_failures_per_email": service.login_limits["email"],
            "max_failures_per_address": service.login_limits["address"],
        },
        "features": {
            "legal": service.legal_enabled,
            "email": service.email_enabled,
            "sso": service.sso_enabled,
        },
        "data_dir": str(service.store.db_path.parent) if service.store.db_path else "./data",
        "templates_dir": "./templates",
        "catalog_dir": "./catalog",
        "providers_dir": "./providers",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "db_path": str(service.store.db_path) if service.store.db_path else "unknown",
    }


@admin_router.put("/api/settings/security")
async def update_security_settings(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update security settings."""
    data = await request.json()
    # Update runtime settings (these would need to be persisted to config file)
    if "token_ttl_hours" in data:
        service.token_ttl = int(data["token_ttl_hours"]) * 3600
    if "password_min_length" in data:
        service.password_min_length = int(data["password_min_length"])
    if "password_iterations" in data:
        service.password_iterations = int(data["password_iterations"])
    if "api_key_prefix" in data:
        service.keys.brand = data["api_key_prefix"]
    if "login_throttle" in data:
        service.login_window = data["login_throttle"].get("window_seconds", service.login_window)
        service.login_limits["email"] = data["login_throttle"].get("max_failures_per_email", service.login_limits["email"])
        service.login_limits["address"] = data["login_throttle"].get("max_failures_per_address", service.login_limits["address"])
    if "allow_signup" in data:
        service.allow_signup = data["allow_signup"]
    if "require_secret_key" in data:
        service.require_secret_key = data["require_secret_key"]
    if "secret_key_min_length" in data:
        service.secret_key_min_length = int(data["secret_key_min_length"])
    return {"updated": True, "note": "Changes are runtime only. Update settings.yaml for persistence."}


@admin_router.post("/api/settings/secret-key")
async def generate_secret_key(service: PlatformService = Depends(get_admin_service)):
    """Generate a new secret key."""
    import secrets
    new_key = secrets.token_urlsafe(32)
    service._configured_secret = new_key
    service._secret_key = new_key
    service.keys = service.keys.__class__(new_key)
    return {"generated": True, "note": "All existing tokens invalidated. Update settings.yaml to persist."}


@admin_router.put("/api/settings/features")
async def update_feature_flags(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update feature flags."""
    data = await request.json()
    if "legal" in data:
        service.legal_enabled = data["legal"]
    if "email" in data:
        service.email_enabled = data["email"]
    if "sso" in data:
        service.sso_enabled = data["sso"]
    return {"updated": True, "note": "Changes are runtime only. Update settings.yaml for persistence."}


@admin_router.put("/api/settings/storage")
async def update_storage_paths(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update storage paths."""
    return {"updated": True, "note": "Storage paths are configured in settings.yaml. Restart required."}


# Billing Settings API
@admin_router.get("/api/billing")
async def get_billing_settings(service: PlatformService = Depends(get_admin_service)):
    """Get billing configuration."""
    billing_cfg = service.catalog.get("billing") or {}
    return {
        "provider": billing_cfg.get("provider", "manual"),
        "stripe": billing_cfg.get("stripe", {}),
        "manual": billing_cfg.get("manual", {}),
    }


@admin_router.put("/api/billing")
async def update_billing_settings(request: Request, service: PlatformService = Depends(get_admin_service)):
    """Update billing configuration."""
    data = await request.json()
    billing_cfg = service.catalog._data.setdefault("billing", {})
    billing_cfg["provider"] = data.get("provider", "manual")
    if "stripe" in data:
        billing_cfg["stripe"] = data["stripe"]
    if "manual" in data:
        billing_cfg["manual"] = data["manual"]
    service.catalog.save()
    return {"updated": True}


@admin_router.get("/api/billing/events")
async def get_billing_events(service: PlatformService = Depends(get_admin_service)):
    """Get recent billing events."""
    # This would come from a billing events log
    return {"events": []}


# Admin Stats
@admin_router.get("/admin/stats")
async def get_admin_stats(service: PlatformService = Depends(get_admin_service)):
    """Get admin dashboard stats."""
    return {
        "total_users": len(service.store.users),
        "total_tenants": len(service.store.tenants),
        "active_subscriptions": sum(1 for t in service.store.tenants.values() if t.status == "active"),
    }


# Settings API
@admin_router.get("/api/settings")
async def get_settings(service: PlatformService = Depends(get_admin_service)):
    """Get platform settings (runtime only)."""
    return {
        "secret_key_set": bool(service._configured_secret),
        "token_ttl_hours": service.token_ttl / 3600,
        "password_iterations": service.password_iterations,
        "password_min_length": service.password_min_length,
        "api_key_prefix": service.keys.brand,
        "allow_signup": service.allow_signup,
        "require_secret_key": service.require_secret_key,
        "secret_key_min_length": service.secret_key_min_length,
        "login_throttle": {
            "window_seconds": service.login_window,
            "max_failures_per_email": service.login_limits["email"],
            "max_failures_per_address": service.login_limits["address"],
        },
        "features": {
            "legal": service.legal_enabled,
            "email": service.email_enabled,
            "sso": service.sso_enabled,
        },
    }


# ============================================
# Context Builders for Pages
# ============================================

async def get_dashboard_context(service: PlatformService, principal: Principal) -> dict[str, Any]:
    """Build dashboard context."""
    # Get stats
    total_users = len(service.store.users)
    total_tenants = len(service.store.tenants)
    total_api_keys = sum(len(service.store.list_keys(t.id)) for t in service.store.tenants.values())

    # Recent activity (simplified)
    recent_tenants = sorted(service.store.tenants.values(), key=lambda t: t.created_at, reverse=True)[:5]

    return {
        "stats": {
            "total_users": total_users,
            "total_tenants": total_tenants,
            "total_api_keys": total_api_keys,
            "active_subscriptions": sum(1 for t in service.store.tenants.values() if t.status == "active"),
        },
        "recent_tenants": [
            {
                "id": t.id,
                "name": t.name,
                "kind": t.kind,
                "plan": t.plan,
                "status": t.status,
                "created_at": formatDate(t.created_at),
            }
            for t in recent_tenants
        ],
    }


async def get_legal_context(service: PlatformService) -> dict[str, Any]:
    """Build legal documents context."""
    if not service.legal_enabled or not service.legal:
        return {"documents": [], "enabled": False}

    documents = []
    for key in service.legal._cache.keys():
        approved = service.legal.get_approved(key)
        latest = service.legal.get_latest(key)
        all_versions = service.legal.get_all_versions(key)
        documents.append({
            "key": key,
            "name": approved.name if approved else (latest.name if latest else key),
            "required": latest.required if latest else True,
            "approved_version": approved.version if approved else None,
            "latest_version": latest.version if latest else None,
            "versions_count": len(all_versions),
            "updated_at": formatDate(latest.updated_at) if latest else None,
        })

    return {"documents": documents, "enabled": True}


async def get_email_context(service: PlatformService) -> dict[str, Any]:
    """Build email templates context."""
    if not service.email_enabled or not service.email:
        return {"templates": [], "enabled": False}

    templates = []
    for key, template in service.email.templates.templates.items():
        templates.append({
            "key": key,
            "name": template.name,
            "subject": template.subject,
            "required_vars": template.required_vars,
            "is_builtin": key in {"verify_email", "password_reset", "invite_member", "billing_receipt", "usage_alert"},
            "has_html": bool(template.html_body),
        })

    return {"templates": templates, "enabled": True}


async def get_sso_context(service: PlatformService) -> dict[str, Any]:
    """Build SSO providers context."""
    if not service.sso_enabled or not service.sso:
        return {"providers": [], "enabled": False}

    providers = []
    for provider in service.sso.store.list_all():
        providers.append({
            "key": provider.key,
            "name": provider.name,
            "type": provider.type.value,
            "enabled": provider.enabled,
            "auto_provision": provider.auto_provision,
            "default_role": provider.default_role,
            "default_account_kind": provider.default_account_kind,
            "default_plan": provider.default_plan,
            "domain_hints": provider.domain_hints,
        })

    return {"providers": providers, "enabled": True}


async def get_plans_context(service: PlatformService) -> dict[str, Any]:
    """Build plans context."""
    plans = []
    for key, plan in service.catalog.plans.items():
        plans.append({
            "key": key,
            "name": plan.name,
            "description": plan.description,
            "price_monthly": plan.price_monthly,
            "price_yearly": plan.price_yearly,
            "currency": service.catalog.currency,
            "audience": plan.audience,
            "features": plan.features,
            "limits": plan.limits,
            "metering": {k: v.to_dict() for k, v in plan.metering.items()},
        })

    return {"plans": plans, "currency": service.catalog.currency}


async def get_billing_context(service: PlatformService) -> dict[str, Any]:
    """Build billing settings context."""
    billing_cfg = service.catalog.get("billing") or {}
    return {
        "provider": billing_cfg.get("provider", "manual"),
        "stripe": billing_cfg.get("stripe", {}),
    }


async def get_roles_context(service: PlatformService) -> dict[str, Any]:
    """Build roles context."""
    roles = service.catalog.get("roles") or {}
    default_member_role = service.catalog.get("default_member_role")
    creator_role = service.catalog.get("creator_role")
    service_key_role = service.catalog.get("service_key_role")

    role_list = []
    for key, role in roles.items():
        role_list.append({
            "key": key,
            "label": role.get("label", key),
            "rank": role.get("rank", 0),
            "permissions": role.get("permissions", []),
            "is_default_member": key == default_member_role,
            "is_creator": key == creator_role,
            "is_service_key": key == service_key_role,
        })

    # Sort by rank descending
    role_list.sort(key=lambda r: -r["rank"])

    return {
        "roles": role_list,
        "default_member_role": default_member_role,
        "creator_role": creator_role,
        "service_key_role": service_key_role,
    }


async def get_account_kinds_context(service: PlatformService) -> dict[str, Any]:
    """Build account kinds context."""
    kinds = service.catalog.get("account_kinds") or {}
    signup_kind = service.catalog.get("signup_kind")
    default_account_kind = service.catalog.get("default_account_kind")

    kind_list = []
    for key, kind in kinds.items():
        kind_list.append({
            "key": key,
            "label": kind.get("label", key),
            "max_members": kind.get("max_members"),
            "signup": kind.get("signup", False),
            "user_creatable": kind.get("user_creatable", False),
            "is_signup_kind": key == signup_kind,
            "is_default": key == default_account_kind,
        })

    return {
        "account_kinds": kind_list,
        "signup_kind": signup_kind,
        "default_account_kind": default_account_kind,
    }


async def get_metrics_context(service: PlatformService) -> dict[str, Any]:
    """Build metrics context."""
    metrics = service.catalog.get("metrics") or {}
    default_metering = service.catalog.get("default_metering") or {}

    metric_list = []
    for key, metric in metrics.items():
        metric_list.append({
            "key": key,
            "name": metric.get("name", key),
            "unit": metric.get("unit", "units"),
            "description": metric.get("description", ""),
            "default_included": default_metering.get(key, {}).get("included"),
            "default_overage": default_metering.get(key, {}).get("overage"),
        })

    return {"metrics": metric_list}


async def get_endpoints_context(service: PlatformService) -> dict[str, Any]:
    """Build endpoints context."""
    endpoints = service.catalog.get("endpoints") or {}
    default_endpoint = service.catalog.get("default_endpoint") or {}

    endpoint_list = []
    for pattern, config in endpoints.items():
        endpoint_list.append({
            "pattern": pattern,
            "permission": config.get("permission"),
            "features": config.get("features", []),
            "metric": config.get("metric"),
            "quantity": config.get("quantity", 1),
            "model_type": config.get("model_type"),
            "periods_from": config.get("periods_from"),
            "public": config.get("public", False),
        })

    return {
        "endpoints": endpoint_list,
        "default_endpoint": default_endpoint,
    }


async def get_messages_context(service: PlatformService) -> dict[str, Any]:
    """Build messages context."""
    messages = service.catalog.get("messages") or {}
    return {"messages": messages}


async def get_settings_context(service: PlatformService) -> dict[str, Any]:
    """Build settings context."""
    return {
        "secret_key_set": bool(service._configured_secret),
        "token_ttl_hours": service.token_ttl / 3600,
        "password_iterations": service.password_iterations,
        "password_min_length": service.password_min_length,
        "api_key_prefix": service.keys.brand,
        "allow_signup": service.allow_signup,
        "require_secret_key": service.require_secret_key,
        "secret_key_min_length": service.secret_key_min_length,
        "login_throttle": {
            "window_seconds": service.login_window,
            "max_failures_per_email": service.login_limits["email"],
            "max_failures_per_address": service.login_limits["address"],
        },
        "features": {
            "legal": service.legal_enabled,
            "email": service.email_enabled,
            "sso": service.sso_enabled,
        },
    }


# ============================================
# Context Builders for New Pages
# ============================================

async def get_currency_context(service: PlatformService) -> dict[str, Any]:
    """Build currency context."""
    currency_cfg = service.catalog.get("currency", {})
    if isinstance(currency_cfg, str):
        currency_cfg = {"code": currency_cfg}
    return {
        "currency": currency_cfg.get("code", "USD"),
        "display_format": currency_cfg.get("display_format", "symbol"),
        "decimal_places": currency_cfg.get("decimal_places", 2),
        "thousands_separator": currency_cfg.get("thousands_separator", ","),
        "decimal_separator": currency_cfg.get("decimal_separator", "."),
        "countries": service.catalog.get("countries", []),
        "default_timezone": service.catalog.get("default_timezone", "UTC"),
        "use_user_timezone": service.catalog.get("use_user_timezone", False),
    }


async def get_account_statuses_context(service: PlatformService) -> dict[str, Any]:
    """Build account statuses context."""
    statuses = service.catalog.get("account_statuses") or {}
    initial_status = service.catalog.get("initial_status", "active")

    status_list = []
    for key, status in statuses.items():
        status_list.append({
            "key": key,
            "blocked": status.get("blocked", False),
            "use_lapsed_plan": status.get("use_lapsed_plan", False),
            "message": status.get("message"),
            "is_initial": key == initial_status,
        })

    return {"statuses": status_list, "initial_status": initial_status}


async def get_default_plans_context(service: PlatformService) -> dict[str, Any]:
    """Build default plans context."""
    plans = []
    for key, plan in service.catalog.plans.items():
        plans.append({
            "key": key,
            "name": plan.name,
        })

    return {
        "default_plans": service.catalog.get("default_plan", {}),
        "lapsed_plan": service.catalog.get("lapsed_plan", "free"),
        "plans": plans,
    }


async def get_special_roles_context(service: PlatformService) -> dict[str, Any]:
    """Build special roles context."""
    roles = service.catalog.get("roles") or {}

    role_list = []
    for key, role in roles.items():
        role_list.append({
            "key": key,
            "label": role.get("label", key),
            "rank": role.get("rank", 0),
            "permissions": role.get("permissions", []),
        })

    return {
        "roles": role_list,
        "creator_role": service.catalog.get("creator_role", "owner"),
        "default_member_role": service.catalog.get("default_member_role", "member"),
        "service_key_role": service.catalog.get("service_key_role", "member"),
    }


async def get_default_metering_context(service: PlatformService) -> dict[str, Any]:
    """Build default metering context."""
    metrics = service.catalog.get("metrics") or {}
    default_metering = service.catalog.get("default_metering") or {}

    metric_list = []
    for key, metric in metrics.items():
        dm = default_metering.get(key, {})
        metric_list.append({
            "key": key,
            "name": metric.get("name", key),
            "unit": metric.get("unit", "units"),
            "included": dm.get("included"),
            "overage_price": dm.get("overage"),
            "overage_action": dm.get("overage", "block") if dm.get("overage") else "block",
        })

    return {"default_metering": metric_list}


async def get_infrastructure_context(service: PlatformService) -> dict[str, Any]:
    """Build infrastructure context."""
    import sys
    import platform

    # Get database info
    db_url = str(service.store.db_path) if service.store.db_path else "unknown"
    db_type = "sqlite" if "sqlite" in db_url else "postgresql" if "postgresql" in db_url else "mysql" if "mysql" in db_url else "unknown"

    return {
        "database_url": db_url,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "db_echo": False,
        "catalog_sources": service.catalog._data.get("catalog", ["builtin"]),
        "storage": {
            "legal_path": "./legal_documents",
            "email_templates_path": "./email_templates",
            "sso_providers_path": "./sso_providers",
            "catalog_override_path": "./catalog_override.yaml",
        },
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "db_type": db_type,
    }


# ============================================
# API Endpoints for New Pages
# ============================================

# Currency & Countries
@admin_router.get("/api/currency")
async def get_currency(service: PlatformService = Depends(get_admin_service)):
    currency_cfg = service.catalog.get("currency", {})
    if isinstance(currency_cfg, str):
        currency_cfg = {"code": currency_cfg}
    return {
        "currency": currency_cfg.get("code", "USD"),
        "display_format": currency_cfg.get("display_format", "symbol"),
        "decimal_places": currency_cfg.get("decimal_places", 2),
        "thousands_separator": currency_cfg.get("thousands_separator", ","),
        "decimal_separator": currency_cfg.get("decimal_separator", "."),
    }

@admin_router.put("/api/currency")
async def update_currency(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    currency_cfg = service.catalog._data.setdefault("currency", {})
    for field in ["code", "display_format", "decimal_places", "thousands_separator", "decimal_separator"]:
        if field in data:
            currency_cfg[field] = data[field]
    service.catalog.save()
    return {"updated": True}

@admin_router.get("/api/countries")
async def list_countries(service: PlatformService = Depends(get_admin_service)):
    return {"countries": service.catalog.get("countries", [])}

@admin_router.post("/api/countries")
async def create_country(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    code = data.get("code", "").upper()
    if not code or len(code) != 2:
        raise HTTPException(status_code=400, detail="code must be 2-letter country code")
    countries = service.catalog._data.setdefault("countries", [])
    if any(c["code"] == code for c in countries):
        raise HTTPException(status_code=409, detail="Country already exists")
    countries.append({
        "code": code,
        "name": data.get("name"),
        "currency": data.get("currency", "USD"),
        "tax_region": data.get("tax_region", code),
        "enabled": data.get("enabled", True),
    })
    service.catalog.save()
    return {"created": True, "code": code}

@admin_router.put("/api/countries/{code}")
async def update_country(code: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    countries = service.catalog.get("countries", [])
    for c in countries:
        if c["code"] == code:
            for field in ["name", "currency", "tax_region", "enabled"]:
                if field in data:
                    c[field] = data[field]
            service.catalog.save()
            return {"updated": True}
    raise HTTPException(status_code=404, detail="Country not found")

@admin_router.delete("/api/countries/{code}")
async def delete_country(code: str, service: PlatformService = Depends(get_admin_service)):
    countries = service.catalog.get("countries", [])
    for i, c in enumerate(countries):
        if c["code"] == code:
            del countries[i]
            service.catalog.save()
            return {"deleted": True}
    raise HTTPException(status_code=404, detail="Country not found")

@admin_router.get("/api/timezone")
async def get_timezone(service: PlatformService = Depends(get_admin_service)):
    return {
        "default_timezone": service.catalog.get("default_timezone", "UTC"),
        "use_user_timezone": service.catalog.get("use_user_timezone", False),
    }

@admin_router.put("/api/timezone")
async def update_timezone(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    if "default_timezone" in data:
        service.catalog._data["default_timezone"] = data["default_timezone"]
    if "use_user_timezone" in data:
        service.catalog._data["use_user_timezone"] = data["use_user_timezone"]
    service.catalog.save()
    return {"updated": True}


# Account Statuses
@admin_router.get("/api/account-statuses")
async def list_account_statuses(service: PlatformService = Depends(get_admin_service)):
    statuses = service.catalog.get("account_statuses") or {}
    initial_status = service.catalog.get("initial_status", "active")
    result = []
    for key, status in statuses.items():
        result.append({
            "key": key,
            "blocked": status.get("blocked", False),
            "use_lapsed_plan": status.get("use_lapsed_plan", False),
            "message": status.get("message"),
            "is_initial": key == initial_status,
        })
    return {"statuses": result, "initial_status": initial_status}

@admin_router.post("/api/account-statuses")
async def create_account_status(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    key = data.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    statuses = service.catalog._data.setdefault("account_statuses", {})
    if key in statuses:
        raise HTTPException(status_code=409, detail="Status already exists")
    statuses[key] = {
        "blocked": data.get("blocked", False),
        "use_lapsed_plan": data.get("use_lapsed_plan", False),
        "message": data.get("message"),
    }
    if data.get("is_initial"):
        service.catalog._data["initial_status"] = key
    service.catalog.save()
    return {"created": True, "key": key}

@admin_router.put("/api/account-statuses/{key}")
async def update_account_status(key: str, request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    statuses = service.catalog.get("account_statuses") or {}
    if key not in statuses:
        raise HTTPException(status_code=404, detail="Status not found")
    for field in ["blocked", "use_lapsed_plan", "message"]:
        if field in data:
            statuses[key][field] = data[field]
    if data.get("is_initial"):
        service.catalog._data["initial_status"] = key
    service.catalog.save()
    return {"updated": True}

@admin_router.delete("/api/account-statuses/{key}")
async def delete_account_status(key: str, service: PlatformService = Depends(get_admin_service)):
    statuses = service.catalog.get("account_statuses") or {}
    if key not in statuses:
        raise HTTPException(status_code=404, detail="Status not found")
    if key == service.catalog.get("initial_status"):
        raise HTTPException(status_code=400, detail="Cannot delete initial status")
    del statuses[key]
    service.catalog.save()
    return {"deleted": True}

@admin_router.put("/api/account-statuses/initial")
async def set_initial_status(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    status = data.get("initial_status")
    if not status:
        raise HTTPException(status_code=400, detail="initial_status is required")
    statuses = service.catalog.get("account_statuses") or {}
    if status not in statuses:
        raise HTTPException(status_code=404, detail="Status not found")
    service.catalog._data["initial_status"] = status
    service.catalog.save()
    return {"updated": True}


# Default Plans
@admin_router.get("/api/default-plans")
async def get_default_plans(service: PlatformService = Depends(get_admin_service)):
    return {
        "default_plans": service.catalog.get("default_plan", {}),
        "lapsed_plan": service.catalog.get("lapsed_plan", "free"),
    }

@admin_router.put("/api/default-plans")
async def update_default_plans(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    if "default_plan" in data:
        service.catalog._data["default_plan"] = data["default_plan"]
    service.catalog.save()
    return {"updated": True}

@admin_router.put("/api/default-plans/lapsed")
async def update_lapsed_plan(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    plan = data.get("lapsed_plan")
    if not plan:
        raise HTTPException(status_code=400, detail="lapsed_plan is required")
    if plan not in service.catalog.plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    service.catalog._data["lapsed_plan"] = plan
    service.catalog.save()
    return {"updated": True}


# Special Roles
@admin_router.get("/api/special-roles")
async def get_special_roles(service: PlatformService = Depends(get_admin_service)):
    return {
        "creator_role": service.catalog.get("creator_role", "owner"),
        "default_member_role": service.catalog.get("default_member_role", "member"),
        "service_key_role": service.catalog.get("service_key_role", "member"),
    }

@admin_router.put("/api/special-roles/creator")
async def update_creator_role(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    role = data.get("creator_role")
    if not role:
        raise HTTPException(status_code=400, detail="creator_role is required")
    if role not in service.catalog.get("roles", {}):
        raise HTTPException(status_code=404, detail="Role not found")
    service.catalog._data["creator_role"] = role
    service.catalog.save()
    return {"updated": True}

@admin_router.put("/api/special-roles/default-member")
async def update_default_member_role(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    role = data.get("default_member_role")
    if not role:
        raise HTTPException(status_code=400, detail="default_member_role is required")
    if role not in service.catalog.get("roles", {}):
        raise HTTPException(status_code=404, detail="Role not found")
    service.catalog._data["default_member_role"] = role
    service.catalog.save()
    return {"updated": True}

@admin_router.put("/api/special-roles/service-key")
async def update_service_key_role(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    role = data.get("service_key_role")
    if not role:
        raise HTTPException(status_code=400, detail="service_key_role is required")
    if role not in service.catalog.get("roles", {}):
        raise HTTPException(status_code=404, detail="Role not found")
    service.catalog._data["service_key_role"] = role
    service.catalog.save()
    return {"updated": True}


# Default Metering
@admin_router.get("/api/default-metering")
async def get_default_metering(service: PlatformService = Depends(get_admin_service)):
    metrics = service.catalog.get("metrics") or {}
    default_metering = service.catalog.get("default_metering") or {}
    result = []
    for key, metric in metrics.items():
        dm = default_metering.get(key, {})
        result.append({
            "key": key,
            "name": metric.get("name", key),
            "unit": metric.get("unit", "units"),
            "included": dm.get("included"),
            "overage_price": dm.get("overage"),
            "overage_action": "bill" if dm.get("overage") else "block",
        })
    return {"default_metering": result}

@admin_router.put("/api/default-metering")
async def update_default_metering(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    default_metering = service.catalog._data.setdefault("default_metering", {})
    if "default_metering" in data:
        for key, config in data["default_metering"].items():
            if key in service.catalog.get("metrics", {}):
                default_metering[key] = {
                    "included": config.get("included"),
                    "overage": config.get("overage"),
                }
    service.catalog.save()
    return {"updated": True}


# Infrastructure
@admin_router.get("/api/infrastructure/database")
async def get_database_config(service: PlatformService = Depends(get_admin_service)):
    import sys
    db_url = str(service.store.db_path) if service.store.db_path else "sqlite:///financial_platform.db"
    return {
        "database_url": db_url,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "echo": False,
    }

@admin_router.put("/api/infrastructure/database")
async def update_database_config(request: Request, service: PlatformService = Depends(get_admin_service)):
    return {"updated": True, "note": "Database config requires settings.yaml update and restart"}

@admin_router.post("/api/infrastructure/database/test")
async def test_database_connection(service: PlatformService = Depends(get_admin_service)):
    # Test by doing a simple query
    _ = len(service.store.users)
    return {"success": True}

@admin_router.get("/api/infrastructure/catalog")
async def get_catalog_sources(service: PlatformService = Depends(get_admin_service)):
    return {"sources": service.catalog._data.get("catalog", ["builtin"])}

@admin_router.put("/api/infrastructure/catalog")
async def update_catalog_sources(request: Request, service: PlatformService = Depends(get_admin_service)):
    data = await request.json()
    sources = data.get("sources", ["builtin"])
    service.catalog._data["catalog"] = sources
    service.catalog.save()
    return {"updated": True, "note": "Requires restart"}

@admin_router.get("/api/infrastructure/storage")
async def get_storage_paths(service: PlatformService = Depends(get_admin_service)):
    return {
        "legal_path": "./legal_documents",
        "email_templates_path": "./email_templates",
        "sso_providers_path": "./sso_providers",
        "catalog_override_path": "./catalog_override.yaml",
    }

@admin_router.put("/api/infrastructure/storage")
async def update_storage_paths(request: Request, service: PlatformService = Depends(get_admin_service)):
    return {"updated": True, "note": "Storage paths require settings.yaml update and restart"}

@admin_router.get("/api/infrastructure/system")
async def get_system_info(service: PlatformService = Depends(get_admin_service)):
    legal_count = len(service.legal._cache) if service.legal_enabled and service.legal else 0
    email_count = len(service.email.templates.templates) if service.email_enabled and service.email else 0
    sso_count = len(service.sso.store._cache) if service.sso_enabled and service.sso else 0
    return {
        "legal_count": legal_count,
        "email_count": email_count,
        "sso_count": sso_count,
    }


# ============================================
# Helper for date formatting in templates
# ============================================

def formatDate(timestamp: float) -> str:
    """Format timestamp for display."""
    if not timestamp:
        return "—"
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


# Add to template globals
templates.env.globals["formatDate"] = formatDate


# ============================================
# Function to install admin portal
# ============================================

def install_admin_portal(app: FastAPI) -> None:
    """Mount admin portal routes and static files."""
    # Mount static files
    app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="admin_static")

    # Include admin router
    app.include_router(admin_router)

    # Root redirect to admin
    @app.get("/admin", include_in_schema=False)
    async def admin_root():
        return RedirectResponse(url="/admin/pages/dashboard")

    # Login page
    @app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
    async def admin_login_page(request: Request):
        return templates.TemplateResponse("login.html", {"request": request})