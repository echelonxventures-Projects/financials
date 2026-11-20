"""Commercial platform: B2C sign-up, B2B organisations, plans, metering, billing webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from financial_model.api.main import create_app
from financial_model.config_loader import get_config
from financial_model.models.business import business_template
from financial_model.platform.billing import BillingError, StripeBilling
from financial_model.platform.plans import load_plans
from financial_model.platform.security import hash_password, sign_token, verify_password, verify_token
from financial_model.platform.service import NotEntitled, PlatformService, QuotaExceeded
from financial_model.platform.store import SQLiteStore, open_store

WEBHOOK_SECRET = "whsec_test"


def _settings(**platform):
    cfg = get_config(builtin=None)
    cfg["platform"].update(
        {
            "enabled": True,
            "database": "sqlite:///:memory:",
            "secret_key": "test-secret",
            "password_iterations": 1_000,
            "billing": {
                "provider": "stripe",
                "stripe": {"secret_key": "sk_test", "webhook_secret": WEBHOOK_SECRET, "prices": {"pro": "price_pro"}},
            },
            **platform,
        }
    )
    return cfg


@pytest.fixture
def client():
    return TestClient(create_app(_settings()))


def _signup(client, email="ada@example.com"):
    r = client.post("/v1/signup", json={"email": email, "password": "correct horse", "name": "Ada"})
    assert r.status_code == 201, r.text
    return r.json()


def _calc(client, headers, model_type="b2c-subscription", years=3, **extra):
    config = business_template(model_type, years=years) if model_type != "dcf" else {"model_type": "dcf"}
    config.update(extra)
    return client.post("/calculate" if model_type != "dcf" else "/dcf", json={"config": config}, headers=headers)


def _stripe_headers(payload: bytes, secret=WEBHOOK_SECRET, ts=None):
    ts = int(ts or time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


class TestSelfHostedModeUnchanged:
    def test_platform_off_by_default(self):
        c = TestClient(create_app(get_config(builtin=None)))
        assert c.post("/v1/signup", json={}).status_code == 404
        assert c.post("/calculate", json={"config": business_template("paas", years=2)}).status_code == 200


class TestB2C:
    def test_signup_then_run_with_key_and_usage_is_metered(self, client):
        account = _signup(client)
        headers = {"X-API-Key": account["api_key"]}
        assert account["tenant"]["kind"] == "individual"
        assert account["tenant"]["plan"] == "free"

        r = _calc(client, headers)
        assert r.status_code == 200, r.text
        assert "Unit Economics" in r.json()["analysis"]
        usage = client.get("/v1/usage", headers=headers).json()
        assert usage["metrics"]["runs"] == 1
        assert usage["runs_remaining"] == usage["runs_limit"] - 1

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
        assert r.status_code == 402
        assert client.post("/v1/keys", json={"name": "second"}, headers=headers).status_code == 402

    def test_login_token_works_like_a_key(self, client):
        _signup(client)
        r = client.post("/v1/login", json={"email": "ADA@example.com", "password": "correct horse"})
        assert r.status_code == 200
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        me = client.get("/v1/me", headers=headers).json()
        assert me["user"]["email"] == "ada@example.com"
        assert me["tenant"]["role"] == "owner"
        assert client.post("/v1/login", json={"email": "ada@example.com", "password": "wrong pass"}).status_code == 401

    def test_duplicate_signup_and_disabled_signup(self, client):
        _signup(client)
        r = client.post("/v1/signup", json={"email": "ada@example.com", "password": "another pw"})
        assert r.status_code == 409
        closed = TestClient(create_app(_settings(allow_signup=False)))
        assert closed.post("/v1/signup", json={"email": "x@example.com", "password": "12345678"}).status_code == 403

    def test_monthly_quota(self):
        c = TestClient(create_app(_settings(plans={"free": {"runs_per_month": 2}})))
        headers = {"X-API-Key": _signup(c)["api_key"]}
        assert _calc(c, headers, years=1).status_code == 200
        assert _calc(c, headers, years=1).status_code == 200
        r = _calc(c, headers, years=1)
        assert r.status_code == 429 and "quota" in r.json()["detail"]

    def test_key_revocation(self, client):
        account = _signup(client)
        headers = {"X-API-Key": account["api_key"]}
        key_id = client.get("/v1/keys", headers=headers).json()["keys"][0]["id"]
        assert client.delete(f"/v1/keys/{key_id}", headers=headers).status_code == 200
        assert client.get("/v1/me", headers=headers).status_code == 401


class TestB2B:
    def _org(self, client):
        _signup(client, "owner@acme.com")
        token = client.post("/v1/login", json={"email": "owner@acme.com", "password": "correct horse"}).json()["token"]
        org = client.post(
            "/v1/organizations", json={"name": "Acme"}, headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert org["kind"] == "organization" and org["plan"] == "team"
        r = client.post(
            "/v1/login", json={"email": "owner@acme.com", "password": "correct horse", "tenant_id": org["id"]}
        )
        return org, {"Authorization": f"Bearer {r.json()['token']}"}

    def test_org_members_roles_and_keys(self, client):
        org, owner = self._org(client)
        # Team plan: every model, Excel export
        assert _calc(client, owner, "dcf").status_code == 200
        assert (
            client.post(
                "/export/excel", json={"config": business_template("b2b-saas", years=2)}, headers=owner
            ).status_code
            == 200
        )

        r = client.post("/v1/members", json={"email": "analyst@acme.com", "role": "viewer"}, headers=owner)
        assert r.status_code == 201
        assert len(client.get("/v1/members", headers=owner).json()["members"]) == 2

        service_key = client.post("/v1/keys", json={"name": "erp-sync"}, headers=owner).json()["api_key"]
        assert _calc(client, {"X-API-Key": service_key}, "b2b-saas").status_code == 200
        assert client.get("/v1/usage", headers=owner).json()["metrics"]["runs"] == 3

    def test_viewer_cannot_run_or_administer(self, client):
        org, owner = self._org(client)
        svc: PlatformService = client.app.state.platform
        svc.add_member(org["id"], "viewer@acme.com", "viewer", password="viewer pass")
        r = client.post(
            "/v1/login", json={"email": "viewer@acme.com", "password": "viewer pass", "tenant_id": org["id"]}
        )
        viewer = {"Authorization": f"Bearer {r.json()['token']}"}
        assert _calc(client, viewer).status_code == 403
        assert client.post("/v1/members", json={"email": "x@acme.com"}, headers=viewer).status_code == 403
        assert client.get("/v1/usage", headers=viewer).status_code == 200

    def test_seat_limit(self):
        c = TestClient(create_app(_settings(plans={"team": {"seats": 2}})))
        _, owner = TestB2B._org(self, c)
        assert c.post("/v1/members", json={"email": "a@acme.com"}, headers=owner).status_code == 201
        assert c.post("/v1/members", json={"email": "b@acme.com"}, headers=owner).status_code == 402

    def test_b2c_plans_not_for_orgs(self, client):
        org, owner = self._org(client)
        svc: PlatformService = client.app.state.platform
        with pytest.raises(Exception, match="not available to organization"):
            svc.set_plan(org["id"], "pro")


class TestBilling:
    def test_checkout_calls_stripe_with_tenant_and_plan(self, client):
        svc: PlatformService = client.app.state.platform
        sent = {}

        def fake_post(url, form, headers):
            sent.update(url=url, form=form, headers=headers)
            return {"url": "https://checkout.stripe.test/c/123"}

        svc.billing.http_post = fake_post
        account = _signup(client)
        r = client.post(
            "/v1/billing/checkout",
            json={"plan": "pro", "success_url": "https://app/ok", "cancel_url": "https://app/no"},
            headers={"X-API-Key": account["api_key"]},
        )
        assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test")
        assert sent["url"].endswith("/checkout/sessions")
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
        assert client.get("/v1/me", headers=headers).json()["tenant"]["plan"]["key"] == "pro"
        assert _calc(client, headers, "dcf").status_code == 200  # Pro includes DCF

        # Replays are ignored
        assert client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body)).json()["updated"] == []

        cancel = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_1"}}}
        body = json.dumps(cancel).encode()
        assert client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body)).status_code == 200
        me = client.get("/v1/me", headers=headers).json()
        assert me["tenant"]["plan"]["key"] == "free" and me["tenant"]["status"] == "canceled"

    def test_past_due_blocks_runs(self, client):
        account = _signup(client)
        svc: PlatformService = client.app.state.platform
        svc.store.update_tenant(account["tenant"]["id"], status="past_due")
        assert _calc(client, {"X-API-Key": account["api_key"]}).status_code == 402

    def test_bad_signatures_rejected(self, client):
        body = b'{"id": "evt_x", "type": "checkout.session.completed", "data": {"object": {}}}'
        assert (
            client.post("/v1/billing/webhook", content=body, headers=_stripe_headers(body, "wrong")).status_code == 400
        )
        old = _stripe_headers(body, ts=time.time() - 3600)
        assert client.post("/v1/billing/webhook", content=body, headers=old).status_code == 400
        assert client.post("/v1/billing/webhook", content=body).status_code == 400

    def test_manual_billing_has_no_checkout(self):
        c = TestClient(create_app(_settings(billing={"provider": "manual"})))
        headers = {"X-API-Key": _signup(c)["api_key"]}
        r = c.post("/v1/billing/checkout", json={"plan": "pro", "success_url": "a", "cancel_url": "b"}, headers=headers)
        assert r.status_code == 400 and "no online checkout" in r.json()["detail"]


class TestBuildingBlocks:
    def test_passwords_and_tokens(self):
        encoded = hash_password("s3cret-pass", iterations=1_000)
        assert verify_password("s3cret-pass", encoded) and not verify_password("nope", encoded)
        token, _ = sign_token({"uid": "u", "tid": "t"}, "k", 60)
        assert verify_token(token, "k")["uid"] == "u"
        assert verify_token(token, "other") is None
        expired, _ = sign_token({"uid": "u"}, "k", -1)
        assert verify_token(expired, "k") is None
        with pytest.raises(ValueError):
            hash_password("short")

    def test_plan_overrides(self):
        plans = load_plans(
            {"pro": {"price_monthly": 29}, "startup": {"audience": "organization", "seats": 10}, "free": None}
        )
        assert plans["pro"].price_monthly == 29 and plans["pro"].charts
        assert plans["startup"].name == "Startup" and plans["startup"].seats == 10
        assert "free" not in plans
        with pytest.raises(ValueError):
            load_plans({"pro": {"bogus": 1}})

    def test_sqlite_file_store_persists(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'p.db'}"
        svc = PlatformService(open_store(url), password_iterations=1_000)
        _, tenant, key = svc.signup_individual("p@example.com", "password1")
        svc.store.close()
        again = PlatformService(open_store(url), password_iterations=1_000)
        assert again.authenticate(api_key=key).tenant.id == tenant.id

    def test_service_checks_without_http(self):
        svc = PlatformService(
            SQLiteStore(), plans=load_plans({"free": {"runs_per_month": 1}}), password_iterations=1_000
        )
        _, _, key = svc.signup_individual("q@example.com", "password1")
        who = svc.authenticate(api_key=key)
        with pytest.raises(NotEntitled):
            svc.check(who, model_type="lbo")
        svc.check(who, model_type="paas")
        svc.record(who.tenant.id)
        with pytest.raises(QuotaExceeded):
            svc.check(who, model_type="paas")

    def test_stripe_subscription_status_mapping(self):
        stripe = StripeBilling({"webhook_secret": WEBHOOK_SECRET})
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
