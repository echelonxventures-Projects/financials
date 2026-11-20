"""
Billing providers: turn a plan choice into a payment, and payment events into plan changes.

The platform never depends on one payment company. A provider implements
:class:`BillingProvider`; the built-ins are

* ``manual`` (default): no online payment. The operator invoices offline and changes
  plans with ``financial-model platform set-plan`` (typical for B2B / enterprise deals).
* ``stripe``: hosted Checkout for self-serve B2C/B2B upgrades and signed webhooks for
  subscription changes. Uses Stripe's REST API over HTTPS with the standard library;
  no Stripe SDK is needed.

Others (Paddle, Lemon Squeezy, Chargebee, an invoicing system...) register under the
``financial_model.billing_providers`` entry point or with :func:`register_billing_provider`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from financial_model.utils.plugins import discover

BILLING_GROUP = "financial_model.billing_providers"


class BillingError(Exception):
    """Billing is not configured, a signature is wrong, or the provider refused a call."""


@dataclass
class BillingEvent:
    """A provider-neutral account change derived from a webhook."""

    id: str
    tenant_id: str | None = None
    customer_id: str | None = None
    plan: str | None = None
    status: str | None = None  # active | past_due | canceled
    raw_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class BillingProvider:
    name = "base"

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or {}

    def checkout_url(
        self,
        tenant_id: str,  # noqa: ARG002
        plan: str,  # noqa: ARG002
        email: str | None,  # noqa: ARG002
        success_url: str,  # noqa: ARG002
        cancel_url: str,  # noqa: ARG002
        seats: int = 1,  # noqa: ARG002
    ) -> str:
        raise BillingError(f"billing provider '{self.name}' has no online checkout; contact the operator")

    def portal_url(self, customer_id: str, return_url: str) -> str:  # noqa: ARG002
        raise BillingError(f"billing provider '{self.name}' has no customer portal")

    def parse_webhook(self, payload: bytes, headers: dict[str, str]) -> list[BillingEvent]:  # noqa: ARG002
        raise BillingError(f"billing provider '{self.name}' does not accept webhooks")


class ManualBilling(BillingProvider):
    """Offline invoicing: plans are changed by the operator."""

    name = "manual"


HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


def _urllib_post(url: str, form: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - fixed https URL
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            body: dict[str, Any] = json.loads(resp.read())
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise BillingError(f"payment provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BillingError(f"payment provider unreachable: {exc.reason}") from exc


class StripeBilling(BillingProvider):
    """Stripe Checkout + webhooks.

    Settings (``platform.billing.stripe``): ``secret_key``, ``webhook_secret`` and
    ``prices`` mapping plan key -> Stripe price id (``{pro: price_123, team: price_456}``).
    Checkout sessions carry the tenant id (``client_reference_id``) and plan (metadata),
    so the webhook can apply the change without any other lookup.
    """

    name = "stripe"
    API = "https://api.stripe.com/v1"
    TOLERANCE_SECONDS = 300

    def __init__(self, settings: dict[str, Any] | None = None, http_post: HttpPost | None = None):
        super().__init__(settings)
        self.http_post = http_post or _urllib_post

    def _secret(self) -> str:
        key = str(self.settings.get("secret_key") or "")
        if not key:
            raise BillingError("platform.billing.stripe.secret_key is not set")
        return key

    def _post(self, path: str, form: dict[str, Any]) -> dict[str, Any]:
        return self.http_post(f"{self.API}{path}", form, {"Authorization": f"Bearer {self._secret()}"})

    def checkout_url(
        self, tenant_id: str, plan: str, email: str | None, success_url: str, cancel_url: str, seats: int = 1
    ) -> str:
        price = (self.settings.get("prices") or {}).get(plan)
        if not price:
            raise BillingError(f"no Stripe price configured for plan '{plan}' (platform.billing.stripe.prices)")
        form: dict[str, Any] = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": tenant_id,
            "line_items[0][price]": price,
            "line_items[0][quantity]": max(1, int(seats)),
            "metadata[tenant_id]": tenant_id,
            "metadata[plan]": plan,
            "subscription_data[metadata][tenant_id]": tenant_id,
            "subscription_data[metadata][plan]": plan,
        }
        if email:
            form["customer_email"] = email
        session = self._post("/checkout/sessions", form)
        url = session.get("url")
        if not url:
            raise BillingError("Stripe did not return a checkout URL")
        return str(url)

    def portal_url(self, customer_id: str, return_url: str) -> str:
        session = self._post("/billing_portal/sessions", {"customer": customer_id, "return_url": return_url})
        return str(session["url"])

    def verify_signature(self, payload: bytes, header: str, now: float | None = None) -> None:
        secret = str(self.settings.get("webhook_secret") or "")
        if not secret:
            raise BillingError("platform.billing.stripe.webhook_secret is not set")
        parts: dict[str, list[str]] = {}
        for item in header.split(","):
            k, _, v = item.strip().partition("=")
            parts.setdefault(k, []).append(v)
        try:
            timestamp = int(parts["t"][0])
        except (KeyError, ValueError) as exc:
            raise BillingError("malformed Stripe-Signature header") from exc
        if abs((now or time.time()) - timestamp) > self.TOLERANCE_SECONDS:
            raise BillingError("webhook timestamp outside tolerance")
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
            raise BillingError("webhook signature does not match")

    def parse_webhook(self, payload: bytes, headers: dict[str, str]) -> list[BillingEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        self.verify_signature(payload, lowered.get("stripe-signature", ""))
        event = json.loads(payload)
        etype = str(event.get("type", ""))
        obj = (event.get("data") or {}).get("object") or {}
        meta = obj.get("metadata") or {}
        base: dict[str, Any] = {
            "id": str(event.get("id")),
            "raw_type": etype,
            "customer_id": obj.get("customer"),
            "data": obj,
        }
        if etype == "checkout.session.completed":
            return [
                BillingEvent(
                    tenant_id=obj.get("client_reference_id") or meta.get("tenant_id"),
                    plan=meta.get("plan"),
                    status="active",
                    **base,
                )
            ]
        if etype in ("customer.subscription.created", "customer.subscription.updated"):
            status = {"active": "active", "trialing": "active", "past_due": "past_due", "unpaid": "past_due"}.get(
                str(obj.get("status")), "canceled"
            )
            return [BillingEvent(tenant_id=meta.get("tenant_id"), plan=meta.get("plan"), status=status, **base)]
        if etype == "customer.subscription.deleted":
            return [BillingEvent(tenant_id=meta.get("tenant_id"), status="canceled", **base)]
        if etype == "invoice.payment_failed":
            return [BillingEvent(status="past_due", **base)]
        return []  # other events are acknowledged and ignored


_PROVIDERS: dict[str, type[BillingProvider]] = {"manual": ManualBilling, "stripe": StripeBilling}


def register_billing_provider(name: str, cls: type[BillingProvider]) -> None:
    _PROVIDERS[name] = cls


def billing_provider(billing_settings: dict[str, Any] | None) -> BillingProvider:
    """Build the provider named by ``platform.billing.provider`` with its own settings block."""
    for name, cls in discover(BILLING_GROUP).items():
        _PROVIDERS.setdefault(name, cls)
    cfg = billing_settings or {}
    name = str(cfg.get("provider") or "manual")
    if name not in _PROVIDERS:
        raise ValueError(f"unknown billing provider '{name}'. Available: {', '.join(sorted(_PROVIDERS))}")
    return _PROVIDERS[name](cfg.get(name) or {})
