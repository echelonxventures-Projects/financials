"""
`financial-model platform ...`: operator commands for the commercial platform.

They act directly on the platform database named in the settings
(``platform.database``), so they work before the API is running and for things
the API deliberately does not expose: creating enterprise accounts, changing a
plan after an offline invoice, issuing keys for a customer, invoices and usage sync.
Plans, prices, roles and account kinds all come from the catalogue.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import typer

platform_app = typer.Typer(help="Operate the commercial platform: accounts, plans, keys, usage.", no_args_is_help=True)

SettingsOpt = typer.Option(None, "--settings", help="Runtime settings YAML (default: FIN_CONFIG_PATH / financial.yaml)")
DatabaseOpt = typer.Option(None, "--database", help="Override platform.database, e.g. sqlite:///platform.db")


def _service(settings: Optional[Path], database: Optional[str]) -> Any:
    from financial_model import config_loader
    from financial_model.platform.service import PlatformService

    if settings is not None:
        os.environ["FIN_CONFIG_PATH"] = str(settings.resolve())
        config_loader._reset_cache()
    cfg = dict(config_loader.get_config().get("platform", {}))
    if database:
        cfg["database"] = database
    try:
        return PlatformService.from_settings(cfg)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _run(fn: Any) -> Any:
    from financial_model.platform.billing import BillingError
    from financial_model.platform.service import PlatformError

    try:
        return fn()
    except (PlatformError, BillingError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _fmt(value: Any, currency: str) -> str:
    return "contact sales" if value is None else f"{value:,.2f} {currency}".strip()


@platform_app.command("plans")
def plans_cmd(settings: Optional[Path] = SettingsOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show the effective catalogue (built-in + settings overrides): prices, allowances, pay-to-use rates."""
    svc = _service(settings, "sqlite:///:memory:")
    cat = svc.catalog
    if as_json:
        typer.echo(json.dumps({"currency": cat.currency, "plans": [p.to_dict() for p in cat.plans.values()]}, indent=2))
        return
    for p in cat.plans.values():
        fee = _fmt(p.price_monthly, cat.currency) + ("/seat" if p.per_seat else "") + "/mo"
        typer.echo(f"{p.key} ({p.name}) - {', '.join(p.audience)} - {fee}")
        for metric, rule in p.metering.items():
            included = "unlimited" if rule.unlimited else f"{rule.included:g} included"
            rates = ", ".join(
                f"{t.unit_price:g}/unit" + (f" up to {t.up_to:g}" if t.up_to is not None else "") for t in rule.tiers
            )
            typer.echo(f"    {metric}: {included}, overage {rule.overage}" + (f" ({rates})" if rates else ""))


@platform_app.command("create-user")
def create_user(
    email: str = typer.Argument(...),
    password: Optional[str] = typer.Option(None, "--password", help="Omit to create a user who cannot log in yet"),
    name: str = typer.Option("", "--name"),
    signup: bool = typer.Option(False, "--signup", help="Also create their self-serve account (signup_kind) and a key"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Create a user (and optionally their self-serve account, B2C by default)."""
    svc = _service(settings, database)
    if signup:
        if not password:
            typer.echo("Error: --signup needs --password", err=True)
            raise typer.Exit(1)
        user, tenant, key = _run(lambda: svc.signup(email, password, name))
        typer.echo(f"user {user.id}  account {tenant.id} ({tenant.kind}, {tenant.plan})\napi key (shown once): {key}")
        return
    user = _run(lambda: svc.create_user(email, password, name))
    typer.echo(f"user {user.id}")


@platform_app.command("create-account")
def create_account(
    name: str = typer.Argument(...),
    owner_email: str = typer.Option(..., "--owner", help="Owner's email (created if new)"),
    kind: Optional[str] = typer.Option(None, "--kind", help="Account kind (default: catalogue default_account_kind)"),
    plan: Optional[str] = typer.Option(None, "--plan", help="Default: the catalogue default_plan for the kind"),
    password: Optional[str] = typer.Option(None, "--password", help="Owner password when creating the owner"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Create an account of any kind (B2B organisation by default)."""
    svc = _service(settings, database)
    owner = svc.store.get_user_by_email(owner_email.strip().lower()) or _run(
        lambda: svc.create_user(owner_email, password)
    )
    account_kind = kind or str(svc.catalog.get("default_account_kind"))
    tenant = _run(lambda: svc.create_account(account_kind, name, owner, plan))
    typer.echo(f"account {tenant.id}  kind {tenant.kind}  plan {tenant.plan}  owner {owner.email}")


@platform_app.command("add-member")
def add_member(
    tenant_id: str = typer.Argument(...),
    email: str = typer.Argument(...),
    role: Optional[str] = typer.Option(None, "--role", help="A catalogue role (default: default_member_role)"),
    password: Optional[str] = typer.Option(None, "--password"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Add a user to an account (respects seats and the kind's member limit)."""
    svc = _service(settings, database)
    user = _run(lambda: svc.add_member(tenant_id, email, role, password))
    typer.echo(f"{user.email} is now {role or svc.catalog.get('default_member_role')} of {tenant_id}")


@platform_app.command("issue-key")
def issue_key(
    tenant_id: str = typer.Argument(...),
    name: str = typer.Option("api", "--name"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Issue a service API key for an account (printed once)."""
    svc = _service(settings, database)
    plain, record = _run(lambda: svc.issue_key(tenant_id, None, name))
    typer.echo(f"key {record.id}\napi key (shown once): {plain}")


@platform_app.command("set-plan")
def set_plan(
    tenant_id: str = typer.Argument(...),
    plan: str = typer.Argument(...),
    status: Optional[str] = typer.Option(None, "--status", help="A catalogue account status (default: initial_status)"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Change an account's plan / status (e.g. after an offline invoice is paid)."""
    svc = _service(settings, database)
    tenant = _run(lambda: svc.set_plan(tenant_id, plan, status))
    typer.echo(f"{tenant.id} -> {tenant.plan} ({tenant.status})")


@platform_app.command("tenants")
def tenants(settings: Optional[Path] = SettingsOpt, database: Optional[str] = DatabaseOpt) -> None:
    """List accounts with kind, plan, status and this month's usage."""
    svc = _service(settings, database)
    for t in svc.store.list_tenants():
        used = ", ".join(f"{k} {v:g}" for k, v in svc.usage(t.id)["metrics"].items()) or "no usage"
        typer.echo(f"{t.id}  {t.kind:<12} {t.plan:<10} {t.status:<9} {used:<30} {t.name}")


@platform_app.command("usage")
def usage(
    tenant_id: str = typer.Argument(...),
    period: Optional[str] = typer.Option(None, "--period", help="YYYY-MM (default: this month, UTC)"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Usage and remaining allowances of one account, as JSON."""
    svc = _service(settings, database)
    typer.echo(json.dumps(_run(lambda: svc.usage(tenant_id, period)), indent=2))


@platform_app.command("invoice")
def invoice(
    tenant_id: Optional[str] = typer.Argument(None, help="Account id; omit for every account"),
    period: Optional[str] = typer.Option(None, "--period", help="YYYY-MM (default: this month, UTC)"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Charges for a month (plan fee, seats, pay-to-use usage) as JSON, for offline invoicing."""
    svc = _service(settings, database)
    ids = [tenant_id] if tenant_id else [t.id for t in svc.store.list_tenants()]
    out = [_run(lambda i=i: svc.invoice(i, period)) for i in ids]
    typer.echo(json.dumps(out[0] if tenant_id else out, indent=2))


@platform_app.command("sync-usage")
def sync_usage(
    period: Optional[str] = typer.Option(None, "--period", help="YYYY-MM (default: this month, UTC)"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Report unreported pay-to-use usage to the billing provider (safe to run on a schedule)."""
    svc = _service(settings, database)
    sent = _run(lambda: svc.sync_usage(period))
    typer.echo(json.dumps({"reported": sent}, indent=2))
