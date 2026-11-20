"""Commercial platform: B2C sign-up, B2B accounts, data-driven plans, pay-to-use, billing webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from financial_model.api.main import create_app
from financial_model.config_loader import _deep_merge, get_config
from financial_model.models.business import business_template
from financial_model.platform.billing import BillingError, StripeBilling
from financial_model.platform.catalog import BUILTIN_PATH, Catalog, CatalogError, MeterRule, builtin_settings
from financial_model.platform.security import KeyFormat, hash_password, sign_token, verify_password, verify_token
from financial_model.platform.service import NotEntitled, PlatformService, QuotaExceeded
from financial_model.platform.store import open_store

WEBHOOK_SECRET = "whsec_test"


def _settings(**platform):
    cfg = get_config(builtin=None)
    base = {
        "enabled": True,
        "database": "sqlite:///:memory:",
        "secret_key": "test-secret",
        "password_iterations": 1_000,
        "billing": {
            "provider": "stripe",
            "stripe": {"secret_key": "sk_test", "webhook_secret": WEBHOOK_SECRET, "prices": {"pro": "price_pro"}},
        },
    }
    cfg["platform"] = _deep_merge(cfg["platform"], _deep_merge(base, platform))
    return cfg


def _client(**platform):
    return TestClient(create_app(_settings(**platform)))


@pytest.fixture
def client():
    return _client()


def _signup(client, email="ada@example.com"):
    r = client.post("/v1/signup", json={"email": email, "password": "correct horse", "name": "Ada"})
    assert r.status_code == 201, r.text
    return r.json()


def _calc(client, headers, model_type="b2c-subscription", years=3):
    if model_type == "dcf":
        return client.post("/dcf", json={"config": {"model_type": "dcf"}}, headers=headers)
    return client.post("/calculate", json={"config": business_template(model_type, years=years)}, headers=headers)


def _stripe_headers(payload: bytes, secret=WEBHOOK_SECRET, ts=None):
    ts = int(ts or time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def _login(client, email, password="correct horse", tenant_id=None):
    body = {"email": email, "password": password, **({"tenant_id": tenant_id} if tenant_id else {})}
    r = client.post("/v1/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


class TestNothingCommercialInCode:
    def test_builtin_catalogue_and_settings_are_data_files(self):
        assert BUILTIN_PATH.suffix == ".yaml"
        cat = Catalog.load()
        assert set(cat.plans) == set(yaml.safe_load(BUILTIN_PATH.read_text(encoding="utf-8"))["plans"])
        assert (
            get_config(builtin=None)["platform"]["billing"]["stripe"]["status_map"]
            == builtin_settings()["billing"]["stripe"]["status_map"]
        )

    def test_platform_off_by_default(self):
        c = TestClient(create_app(get_config(builtin=None)))
        assert c.post("/v1/signup", json={}).status_code == 404
        assert c.post("/calculate", json={"config": business_template("paas", years=2)}).status_code == 200


class TestB2C:
    def test_signup_then_run_with_key_and_usage_is_metered(self, client):
        account = _signup(client)
        headers = {"X-API-Key": account["api_key"]}
        assert account["tenant"] == {"id": account["tenant"]["id"], "kind": "individual", "plan": "free"}
        r = _calc(client, headers)
        assert r.status_code == 200, r.text
        assert "Unit Economics" in r.json()["analysis"]
        usage = client.get("/v1/usage", headers=headers).json()
        assert usage["metrics"]["runs"] == 1
        assert usage["allowances"]["runs"]["remaining"] == usage["allowances"]["runs"]["included"] - 1

    def test_failed_runs_are_not_counted(self, client):
        headers = {"X-API-Key": _signup(client)["api_key"]}
        bad = business_template("paas", years=2)
        bad["periods"]["type"] = "weekly"
        assert client.post("/calculate", json={"config": bad}, headers=headers).status_code >= 400
        assert client.get("/v1/usage", headers=headers).json()["metrics"].get("runs", 0) == 0

    def test_auth_required(self, client):
        assert client.post("/calculate", json={"config": {}}).status_code == 401
        assert client.get("/models").status_code == 401
        assert client.get("/health").status_code == 200
        assert client.get("/v1/plans").status_code == 200
        assert client.post("/calculate", json={"config": {}}, headers={"X-API-Key": "fm_nope_x"}).status_code == 401

    def test_free_plan_limits(self, client):
        headers = {"X-API-Key": _signup(client)["api_key"]}
        r = _calc(client, headers, "dcf")
        assert r.status_code == 402 and "does not include 'dcf'" in r.json()["detail"]
        too_long = business_template("paas", years=3)
        too_long["periods"] = {"start_year": 2025, "years": 6, "type": "monthly"}  # 72 > 60 periods
        assert client.post("/calculate", json={"config": too_long}, headers=headers).status_code == 402
        r = client.post("/export/excel", json={"config": business_template("paas", years=2)}, headers=headers)
        assert r.status_code == 402 and "export:excel" in r.json()["detail"]
        assert client.post("/v1/keys", json={"name": "second"}, headers=headers).status_code == 402

    def test_login_token_works_like_a_key(self, client):
        _signup(client)
        headers = _login(client, "ADA@example.com")
        me = client.get("/v1/me", headers=headers).json()
        assert me["user"]["email"] == "ada@example.com"
        assert me["tenant"]["role"] == "owner" and me["plan"]["key"] == "free"
        assert client.post("/v1/login", json={"email": "ada@example.com", "password": "wrong pass"}).status_code == 401

    def test_signup_rules_come_from_settings(self, client):
        _signup(client)
        assert client.post("/v1/signup", json={"email": "ada@example.com", "password": "another pw"}).status_code == 409
        assert client.post("/v1/signup", json={"email": "b@example.com", "password": "short"}).status_code == 400
        closed = _client(allow_signup=False)
        assert closed.post("/v1/signup", json={"email": "x@example.com", "password": "12345678"}).status_code == 403
        lenient = _client(password_min_length=4)
        assert lenient.post("/v1/signup", json={"email": "y@example.com", "password": "abcd"}).status_code == 201

    def test_monthly_allowance_from_settings(self):
        c = _client(plans={"free": {"metering": {"runs": {"included": 2}}}})
        headers = {"X-API-Key": _signup(c)["api_key"]}
        assert _calc(c, headers, years=1).status_code == 200
        assert _calc(c, headers, years=1).status_code == 200
        r = _calc(c, headers, years=1)
        assert r.status_code == 429 and "allowance of 2 runs" in r.json()["detail"]

    def test_key_revocation_and_prefix_setting(self):
        c = _client(api_key_prefix="acme")
        account = _signup(c)
        assert account["api_key"].startswith("acme_")
        headers = {"X-API-Key": account["api_key"]}
        key_id = c.get("/v1/keys", headers=headers).json()["keys"][0]["id"]
        assert c.delete(f"/v1/keys/{key_id}", headers=headers).status_code == 200
        assert c.get("/v1/me", headers=headers).status_code == 401


class TestPayToUse:
    def test_payg_bills_instead_of_blocking_and_invoices(self):
        c = _client(default_plan={"individual": "payg"})
        headers = {"X-API-Key": _signup(c)["api_key"]}
        for _ in range(3):
            assert _calc(c, headers, "dcf").status_code == 200  # payg includes every model
        bill = c.get("/v1/invoice", headers=headers).json()
        assert bill["plan"] == "payg" and bill["currency"] == "USD"
        [line] = bill["lines"]
        assert line["metric"] == "runs" and line["amount"] == pytest.approx(3 * 0.02)
        assert bill["total"] == pytest.approx(0.06)

    def test_tiered_prices_caps_and_included(self):
        rule = MeterRule.from_dict(
            "runs",
            {
                "included": 100,
                "overage": "bill",
                "tiers": [{"up_to": 1000, "unit_price": 0.05}, {"up_to": None, "unit_price": 0.01}],
                "cap": 5000,
            },
        )
        amount, lines = rule.charge(2100)  # 2000 billable: 1000 @ 0.05 + 1000 @ 0.01
        assert amount == pytest.approx(60.0) and len(lines) == 2
        assert rule.refusal(4999, 1) is None
        assert rule.refusal(5000, 1) == ("cap_reached", {"cap": 5000.0, "metric": "runs"})
        assert MeterRule.from_dict("runs", {"included": None}).refusal(10**9, 1) is None
        with pytest.raises(CatalogError):
            MeterRule.from_dict("runs", {"overage": "bill"})  # billing needs a price

    def test_price_change_is_configuration(self):
        c = _client(
            default_plan={"individual": "payg"},
            plans={"payg": {"metering": {"runs": {"tiers": None, "unit_price": 1.5}}}},
            currency="EUR",
        )
        headers = {"X-API-Key": _signup(c)["api_key"]}
        assert _calc(c, headers).status_code == 200
        bill = c.get("/v1/invoice", headers=headers).json()
        assert bill["currency"] == "EUR" and bill["total"] == pytest.approx(1.5)

    def test_per_seat_fee_on_invoice(self, client):
        svc: PlatformService = client.app.state.platform
        owner = svc.create_user("o@acme.test", "password1")
        org = svc.create_account("organization", "Acme", owner)
        svc.add_member(org.id, "m@acme.test")
        [line] = svc.invoice(org.id)["lines"]
        assert line["per_seat"] and line["quantity"] == 2
        assert line["amount"] == pytest.approx(2 * svc.plans["team"].price_monthly)

    def test_sync_usage_reports_only_billable_units_once(self):
        sent = []
        stripe = StripeBilling(
            {**builtin_settings()["billing"]["stripe"], "secret_key": "sk", "meters": {"runs": "model_runs"}},
            http_post=lambda url, form, headers, timeout: sent.append((url, form)) or {},
        )
        catalog = Catalog.load(overrides={"plans": {"payg": {"metering": {"runs": {"included": 2}}}}})
        svc = PlatformService(open_store("sqlite:///:memory:"), catalog, stripe, password_iterations=1_000)
        _, tenant, _ = svc.signup("p@example.com", "password1")
        svc.set_plan(tenant.id, "payg")
        svc.store.update_tenant(tenant.id, billing_customer_id="cus_9")
        svc.record(tenant.id, "runs", 5)
        assert svc.sync_usage() == [{"account": tenant.id, "metric": "runs", "quantity": 3.0}]
        assert svc.sync_usage() == []  # nothing new
        svc.record(tenant.id, "runs", 1)
        assert svc.sync_usage()[0]["quantity"] == 1.0
        assert sent[0][0].endswith("/billing/meter_events")
        assert sent[0][1]["event_name"] == "model_runs" and sent[0][1]["payload[value]"] == "3"


class TestB2B:
    def _org(self, client):
        _signup(client, "owner@acme.com")
        token = _login(client, "owner@acme.com")
        org = client.post("/v1/accounts", json={"name": "Acme"}, headers=token).json()
        assert org["kind"] == "organization" and org["plan"] == "team"
        return org, _login(client, "owner@acme.com", tenant_id=org["id"])

    def test_org_members_roles_and_keys(self, client):
        org, owner = self._org(client)
        assert _calc(client, owner, "dcf").status_code == 200
        r = client.post("/export/excel", json={"config": business_template("b2b-saas", years=2)}, headers=owner)
        assert r.status_code == 200
        assert (
            client.post("/v1/members", json={"email": "analyst@acme.com", "role": "viewer"}, headers=owner).status_code
            == 201
        )
        assert len(client.get("/v1/members", headers=owner).json()["members"]) == 2
        service_key = client.post("/v1/keys", json={"name": "erp-sync"}, headers=owner).json()["api_key"]
        assert _calc(client, {"X-API-Key": service_key}, "b2b-saas").status_code == 200
        assert client.get("/v1/usage", headers=owner).json()["metrics"]["runs"] == 3

    def test_permissions_come_from_roles(self, client):
        org, owner = self._org(client)
        svc: PlatformService = client.app.state.platform
        svc.add_member(org["id"], "viewer@acme.com", "viewer", password="viewer pass")
        viewer = _login(client, "viewer@acme.com", "viewer pass", org["id"])
        r = _calc(client, viewer)
        assert r.status_code == 403 and "'run' permission" in r.json()["detail"]
        assert client.post("/v1/members", json={"email": "x@acme.com"}, headers=viewer).status_code == 403
        assert client.get("/v1/usage", headers=viewer).status_code == 200

    def test_custom_role_and_endpoint_rule(self):
        c = _client(
            roles={"analyst": {"rank": 15, "permissions": ["view", "run"]}},
            endpoints={
                "POST /export/excel": {"permission": "export_files", "metric": "runs", "features": ["export:excel"]}
            },
        )
        org, owner = TestB2B._org(self, c)
        svc: PlatformService = c.app.state.platform
        svc.add_member(org["id"], "an@acme.com", "analyst", password="analyst pw")
        analyst = _login(c, "an@acme.com", "analyst pw", org["id"])
        assert _calc(c, analyst).status_code == 200
        body = {"config": business_template("paas", years=1)}
        assert c.post("/export/excel", json=body, headers=analyst).status_code == 403  # no export_files
        assert c.post("/export/excel", json=body, headers=owner).status_code == 200  # owner has "*"

    def test_cannot_grant_above_own_rank(self, client):
        org, owner = self._org(client)
        svc: PlatformService = client.app.state.platform
        svc.add_member(org["id"], "admin@acme.com", "admin", password="admin pass")
        admin = _login(client, "admin@acme.com", "admin pass", org["id"])
        assert (
            client.post("/v1/members", json={"email": "z@acme.com", "role": "owner"}, headers=admin).status_code == 403
        )
        assert (
            client.post("/v1/members", json={"email": "z@acme.com", "role": "member"}, headers=admin).status_code == 201
        )

    def test_seat_limit_from_settings(self):
        c = _client(plans={"team": {"limits": {"seats": 2}}})
        _, owner = TestB2B._org(self, c)
        assert c.post("/v1/members", json={"email": "a@acme.com"}, headers=owner).status_code == 201
        assert c.post("/v1/members", json={"email": "b@acme.com"}, headers=owner).status_code == 402

    def test_audience_rules(self, client):
        org, _ = self._org(client)
        svc: PlatformService = client.app.state.platform
        with pytest.raises(Exception, match="not available to organization"):
            svc.set_plan(org["id"], "pro")


class TestBilling:
    def test_checkout_uses_configured_endpoint_and_fields(self):
        c = _client(
            billing={
                "stripe": {
                    "api_base": "https://stripe.example/v9",
                    "checkout": {"mode": "subscription", "allow_promotion_codes": "true"},
                }
            }
        )
        svc: PlatformService = c.app.state.platform
        sent = {}

        def fake_post(url, form, headers, timeout):
            sent.update(url=url, form=form, headers=headers, timeout=timeout)
            return {"url": "https://checkout.stripe.test/c/123"}

        svc.billing.http_post = fake_post
        account = _signup(c)
        r = c.post(
            "/v1/billing/checkout",
            json={"plan": "pro", "success_url": "https://app/ok", "cancel_url": "https://app/no"},
            headers={"X-API-Key": account["api_key"]},
        )
        assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test")
        assert sent["url"] == "https://stripe.example/v9/checkout/sessions"
        assert sent["form"]["allow_promotion_codes"] == "true"
        assert sent["form"]["client_reference_id"] == account["tenant"]["id"]
        assert sent["form"]["metadata[plan]"] == "pro"
        assert sent["headers"]["Authorization"] == "Bearer sk_test"

    def test_webhook_upgrades_then_cancels(self, client):
        account = _signup(client)
        tenant_id, headers = account["tenant"]["id"], {"X-API-Key": account["api_key"]}
        event = {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": tenant_id, "customer": "cus_1", "metadata": {"plan": "pro"}}},
        }
        body = json.dumps(event).encode()
        r = client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body))
        assert r.status_code == 200 and r.json()["updated"] == [tenant_id]
        assert client.get("/v1/me", headers=headers).json()["plan"]["key"] == "pro"
        assert _calc(client, headers, "dcf").status_code == 200
        assert client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body)).json()["updated"] == []

        cancel = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_1"}}}
        body = json.dumps(cancel).encode()
        assert client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body)).status_code == 200
        me = client.get("/v1/me", headers=headers).json()
        assert me["tenant"]["status"] == "canceled" and me["plan"]["key"] == "free"  # lapsed_plan limits

    def test_event_handling_is_configuration(self):
        c = _client(
            billing={
                "stripe": {
                    "events": {
                        "checkout.session.completed": None,
                        "charge.refunded": {"status": "past_due", "tenant_from": "metadata"},
                    }
                }
            }
        )
        account = _signup(c)
        tid = account["tenant"]["id"]
        ignored = json.dumps(
            {"id": "e1", "type": "checkout.session.completed", "data": {"object": {"client_reference_id": tid}}}
        ).encode()
        assert c.post("/v1/billing/webhook", content=ignored, headers=_stripe_headers(ignored)).json()["received"] == 0
        refund = json.dumps(
            {"id": "e2", "type": "charge.refunded", "data": {"object": {"metadata": {"tenant_id": tid}}}}
        ).encode()
        assert c.post("/v1/billing/webhook", content=refund, headers=_stripe_headers(refund)).json()["updated"] == [tid]
        r = _calc(c, {"X-API-Key": account["api_key"]})
        assert r.status_code == 402 and "past due" in r.json()["detail"]

    def test_bad_signatures_rejected(self, client):
        body = b'{"id": "evt_x", "type": "checkout.session.completed", "data": {"object": {}}}'
        assert (
            client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body, "wrong")).status_code == 400
        )
        old = _stripe_headers(body, ts=time.time() - 3600)
        assert client.post("/v1/billing/webhook", content=body, headers=old).status_code == 400
        assert client.post("/v1/billing/webhook", content=body).status_code == 400

    def test_manual_billing_has_no_checkout(self):
        c = _client(billing={"provider": "manual"})
        headers = {"X-API-Key": _signup(c)["api_key"]}
        r = c.post("/v1/billing/checkout", json={"plan": "pro", "success_url": "a", "cancel_url": "b"}, headers=headers)
        assert r.status_code == 400 and "no online checkout" in r.json()["detail"]

    def test_stripe_subscription_status_mapping(self):
        stripe = StripeBilling({**builtin_settings()["billing"]["stripe"], "webhook_secret": WEBHOOK_SECRET})
        event = {
            "id": "e",
            "type": "customer.subscription.updated",
            "data": {"object": {"status": "past_due", "customer": "c", "metadata": {"tenant_id": "t", "plan": "pro"}}},
        }
        body = json.dumps(event).encode()
        [parsed] = stripe.parse_webhook(body, _stripe_headers(body))
        assert (parsed.tenant_id, parsed.plan, parsed.status) == ("t", "pro", "past_due")
        with pytest.raises(BillingError):
            StripeBilling({}).parse_webhook(body, _stripe_headers(body))


class TestCatalogue:
    def test_replace_the_whole_catalogue(self, tmp_path):
        mine = {
            "currency": "INR",
            "account_kinds": {"team": {"label": "Team", "max_members": 5, "signup": True, "user_creatable": True}},
            "signup_kind": "team",
            "default_account_kind": "team",
            "default_plan": "starter",
            "lapsed_plan": "starter",
            "roles": {"lead": {"rank": 2, "permissions": ["*"]}, "guest": {"rank": 1, "permissions": ["view"]}},
            "creator_role": "lead",
            "default_member_role": "guest",
            "service_key_role": "guest",
            "account_statuses": {"ok": {"blocked": False}},
            "initial_status": "ok",
            "metrics": {"runs": {"label": "Runs"}},
            "default_endpoint": {"permission": "view"},
            "endpoints": {
                "GET /health": {"public": True},
                "POST /v1/signup": {"public": True},
                "POST /calculate": {"permission": "view", "metric": "runs"},
            },
            "plans": {
                "starter": {
                    "name": "Starter",
                    "audience": ["team"],
                    "price": {"monthly": 999},
                    "metering": {"runs": {"included": 1}},
                }
            },
        }
        path = tmp_path / "catalog.yaml"
        path.write_text(yaml.safe_dump(mine), encoding="utf-8")
        c = _client(catalog=[str(path)])
        account = _signup(c)
        assert account["tenant"]["kind"] == "team" and account["tenant"]["plan"] == "starter"
        headers = {"X-API-Key": account["api_key"]}
        assert _calc(c, headers).status_code == 200
        assert _calc(c, headers).status_code == 429
        assert c.get("/v1/plans").status_code == 401  # not public in this catalogue
        svc: PlatformService = c.app.state.platform
        assert svc.invoice(account["tenant"]["id"])["currency"] == "INR"

    def test_messages_are_configurable(self):
        c = _client(messages={"bad_key": "Clé API invalide"})
        r = c.post("/calculate", json={"config": {}}, headers={"X-API-Key": "fm_nope_x"})
        assert r.status_code == 401 and r.json()["detail"] == "Clé API invalide"
        assert (
            Catalog.load().message("allowance_reached", included=5, metric="runs")
            == "monthly allowance of 5 runs reached"
        )

    def test_inconsistent_catalogue_is_rejected(self):
        with pytest.raises(CatalogError, match="default_plan"):
            Catalog.load(overrides={"default_plan": {"individual": "nope", "organization": "team"}})
        with pytest.raises(CatalogError, match="unknown fields"):
            Catalog.load(overrides={"plans": {"pro": {"bogus": 1}}})
        with pytest.raises(CatalogError, match="not a defined metric"):
            Catalog.load(overrides={"endpoints": {"POST /calculate": {"metric": "nope"}}})

    def test_remove_and_add_plans(self):
        cat = Catalog.load(
            overrides={
                "plans": {"business": None, "startup": {"audience": ["organization"], "price": {"monthly": 199}}}
            }
        )
        assert "business" not in cat.plans and cat.plans["startup"].price_monthly == 199

    def test_building_blocks(self, tmp_path):
        encoded = hash_password("s3cret-pass", iterations=1_000, min_length=8)
        assert verify_password("s3cret-pass", encoded) and not verify_password("nope", encoded)
        with pytest.raises(ValueError):
            hash_password("short", iterations=1_000, min_length=8)
        token, _ = sign_token({"uid": "u", "tid": "t"}, "k", 60)
        assert verify_token(token, "k")["uid"] == "u" and verify_token(token, "other") is None
        assert KeyFormat("acme").prefix_of("acme_ab12_secret_with_underscores") == "ab12"
        assert KeyFormat("acme").prefix_of("fm_ab12_x") is None

        url = f"sqlite:///{tmp_path / 'p.db'}"
        svc = PlatformService(open_store(url), password_iterations=1_000)
        _, tenant, key = svc.signup("p@example.com", "password1")
        svc.store.close()
        assert PlatformService(open_store(url)).authenticate(api_key=key).tenant.id == tenant.id

    def test_service_checks_without_http(self):
        cat = Catalog.load(overrides={"plans": {"free": {"metering": {"runs": {"included": 1}}}}})
        svc = PlatformService(catalog=cat, password_iterations=1_000)
        _, _, key = svc.signup("q@example.com", "password1")
        who = svc.authenticate(api_key=key)
        with pytest.raises(NotEntitled):
            svc.check(who, model_type="lbo")
        svc.check(who, model_type="paas", metric="runs")
        svc.record(who.tenant.id, "runs")
        with pytest.raises(QuotaExceeded):
            svc.check(who, model_type="paas", metric="runs")
