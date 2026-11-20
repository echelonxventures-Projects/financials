"""
`financial-model platform ...`: operator commands for the commercial platform.

They act directly on the platform database named in the settings
(``platform.database``), so they work before the API is running and for things
the API deliberately does not expose: creating enterprise accounts, changing a
plan after an offline (manual) invoice, issuing keys for a customer, usage reports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

platform_app = typer.Typer(help="Operate the commercial platform: accounts, plans, keys, usage.", no_args_is_help=True)

SettingsOpt = typer.Option(None, "--settings", help="Runtime settings YAML (default: FIN_CONFIG_PATH / financial.yaml)")
DatabaseOpt = typer.Option(None, "--database", help="Override platform.database, e.g. sqlite:///platform.db")


def _service(settings: Optional[Path], database: Optional[str]):  # noqa: ANN202
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


def _run(fn):  # noqa: ANN001, ANN202
    from financial_model.platform.billing import BillingError
    from financial_model.platform.service import PlatformError

    try:
        return fn()
    except (PlatformError, BillingError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@platform_app.command("plans")
def plans_cmd(settings: Optional[Path] = SettingsOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """List the plan catalogue (defaults plus settings overrides)."""
    svc = _service(settings, "sqlite:///:memory:")
    if as_json:
        typer.echo(json.dumps([p.to_dict() for p in svc.plans.values()], indent=2))
        return
    for p in svc.plans.values():
        price = (
            "contact sales"
            if p.price_monthly is None
            else f"${p.price_monthly:,.0f}/mo" + (" per seat" if p.per_seat else "")
        )
        runs = "unlimited" if p.runs_per_month is None else f"{p.runs_per_month:,}"
        typer.echo(
            f"{p.key:<12} {p.name:<12} {p.audience:<13} {price:<20} runs/mo {runs:<10} seats {p.seats or 'unlimited'}"
        )


@platform_app.command("create-user")
def create_user(
    email: str = typer.Argument(...),
    password: Optional[str] = typer.Option(None, "--password", help="Omit to create a user who cannot log in yet"),
    name: str = typer.Option("", "--name"),
    individual: bool = typer.Option(False, "--individual", help="Also create a personal (B2C) account with a key"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Create a user (and optionally their personal B2C account)."""
    svc = _service(settings, database)
    if individual:
        if not password:
            typer.echo("Error: --individual needs --password", err=True)
            raise typer.Exit(1)
        user, tenant, key = _run(lambda: svc.signup_individual(email, password, name))
        typer.echo(f"user {user.id}  tenant {tenant.id} ({tenant.plan})\napi key (shown once): {key}")
        return
    user = _run(lambda: svc.create_user(email, password, name))
    typer.echo(f"user {user.id}")


@platform_app.command("create-org")
def create_org(
    name: str = typer.Argument(...),
    owner_email: str = typer.Option(..., "--owner", help="Owner's email (created if new)"),
    plan: Optional[str] = typer.Option(None, "--plan", help="Default: platform.default_org_plan"),
    password: Optional[str] = typer.Option(None, "--password", help="Owner password when creating the owner"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Create a B2B organisation account."""
    svc = _service(settings, database)
    owner = svc.store.get_user_by_email(owner_email.strip().lower()) or _run(
        lambda: svc.create_user(owner_email, password)
    )
    tenant = _run(lambda: svc.create_organization(name, owner, plan))
    typer.echo(f"organization {tenant.id}  plan {tenant.plan}  owner {owner.email}")


@platform_app.command("add-member")
def add_member(
    tenant_id: str = typer.Argument(...),
    email: str = typer.Argument(...),
    role: str = typer.Option("member", "--role", help="owner | admin | member | viewer"),
    password: Optional[str] = typer.Option(None, "--password"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Add a user to an organisation (respects the plan's seat limit)."""
    svc = _service(settings, database)
    user = _run(lambda: svc.add_member(tenant_id, email, role, password))
    typer.echo(f"{user.email} is now {role} of {tenant_id}")


@platform_app.command("issue-key")
def issue_key(
    tenant_id: str = typer.Argument(...),
    name: str = typer.Option("api", "--name"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Issue an API key for an account (printed once)."""
    svc = _service(settings, database)
    plain, record = _run(lambda: svc.issue_key(tenant_id, None, name))
    typer.echo(f"key {record.id}\napi key (shown once): {plain}")


@platform_app.command("set-plan")
def set_plan(
    tenant_id: str = typer.Argument(...),
    plan: str = typer.Argument(...),
    status: str = typer.Option("active", "--status", help="active | past_due | canceled"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Change an account's plan (e.g. after an offline invoice is paid)."""
    svc = _service(settings, database)
    tenant = _run(lambda: svc.set_plan(tenant_id, plan, status))
    typer.echo(f"{tenant.id} -> {tenant.plan} ({tenant.status})")


@platform_app.command("tenants")
def tenants(settings: Optional[Path] = SettingsOpt, database: Optional[str] = DatabaseOpt) -> None:
    """List accounts with plan, status and this month's runs."""
    svc = _service(settings, database)
    for t in svc.store.list_tenants():
        runs = int(svc.usage(t.id)["metrics"].get("runs", 0))
        typer.echo(f"{t.id}  {t.kind:<12} {t.plan:<10} {t.status:<9} runs {runs:<8} {t.name}")


@platform_app.command("usage")
def usage(
    tenant_id: str = typer.Argument(...),
    period: Optional[str] = typer.Option(None, "--period", help="YYYY-MM (default: this month, UTC)"),
    settings: Optional[Path] = SettingsOpt,
    database: Optional[str] = DatabaseOpt,
) -> None:
    """Usage of one account as JSON (feed for invoicing / overage billing)."""
    svc = _service(settings, database)
    typer.echo(json.dumps(_run(lambda: svc.usage(tenant_id, period)), indent=2))
