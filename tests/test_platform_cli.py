"""`financial-model platform` operator commands (core install: no web framework needed)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from financial_model.cli.main import app

runner = CliRunner()


def test_operator_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_PLATFORM_PASSWORD_ITERATIONS", "1000")
    db = ["--database", f"sqlite:///{tmp_path / 'platform.db'}"]

    out = runner.invoke(app, ["platform", "plans", "--json"])
    assert out.exit_code == 0
    assert {p["key"] for p in json.loads(out.output)["plans"]} >= {"free", "payg", "pro", "team", "enterprise"}
    assert "overage bill" in runner.invoke(app, ["platform", "plans"]).output

    out = runner.invoke(app, ["platform", "create-account", "Acme", "--owner", "cfo@acme.test", "--plan", "team", *db])
    assert out.exit_code == 0, out.output
    tenant = out.output.split()[1]

    assert (
        runner.invoke(app, ["platform", "add-member", tenant, "fp&a@acme.test", "--role", "admin", *db]).exit_code == 0
    )
    out = runner.invoke(app, ["platform", "issue-key", tenant, *db])
    assert "fm_" in out.output

    out = runner.invoke(app, ["platform", "set-plan", tenant, "pro", *db])
    assert out.exit_code == 1 and "not available to organization" in out.output

    assert runner.invoke(app, ["platform", "set-plan", tenant, "enterprise", *db]).exit_code == 0
    usage = json.loads(runner.invoke(app, ["platform", "usage", tenant, *db]).output)
    assert usage["plan"] == "enterprise" and usage["seats_used"] == 2

    out = runner.invoke(
        app, ["platform", "create-user", "solo@example.com", "--signup", "--password", "longenough", *db]
    )
    assert out.exit_code == 0 and "fm_" in out.output
    listing = runner.invoke(app, ["platform", "tenants", *db]).output
    assert "organization" in listing and "individual" in listing

    bills = json.loads(runner.invoke(app, ["platform", "invoice", *db]).output)
    assert len(bills) == 2 and all("total" in b for b in bills)
    assert json.loads(runner.invoke(app, ["platform", "sync-usage", *db]).output) == {"reported": []}
