"""
Billing providers: turn a plan choice into a payment, payment events into account
changes, and pay-to-use usage into metered charges.

The platform never depends on one payment company, and no provider detail is fixed
in code: each provider reads the settings block named after it under
``platform.billing`` (built-in defaults in ``settings.yaml``). Built-ins:

* ``manual``: no online payment. The operator invoices offline (``financial-model
  platform invoice``) and applies plans with ``financial-model platform set-plan``.
* ``stripe``: hosted Checkout, signed webhooks and billing-meter usage events over
  Stripe's REST API with the standard library (no SDK). Endpoint URL, tolerance,
  checkout fields, status mapping and event handling are all settings.

Others (Paddle, Lemon Squeezy, Chargebee, an ERP...) register under the
``financial_model.billing_providers`` entry point or with :func:`register_billing_provider`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
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
    status: str | None = None  # an account status from the catalogue
    raw_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class BillingProvider:
    name = "base"

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or {}

    def setting(self, key: str) -> Any:
        if key not in self.settings:
            raise BillingError(f"platform.billing.{self.name}.{key} is not set")
        return self.settings[key]

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

    def report_usage(self, customer_id: str, metric: str, quantity: float, identifier: str) -> bool:  # noqa: ARG002
        """Send billable pay-to-use units to the provider; False = not supported (invoice offline)."""
        return False


class ManualBilling(BillingProvider):
    """Offline invoicing: plans are changed and usage is invoiced by the operator."""

    name = "manual"


HttpPost = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


def _urllib_post(url: str, form: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise BillingError(f"refusing to call a non-HTTPS billing endpoint: {url}")
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - https only
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body: dict[str, Any] = json.loads(resp.read())
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise BillingError(f"payment provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BillingError(f"payment provider unreachable: {exc.reason}") from exc


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Nested mapping -> Stripe-style form fields (``a[b][c]=v``)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    else:
        out[prefix] = value


class StripeBilling(BillingProvider):
    """Stripe Checkout, webhooks and billing-meter events, driven by ``platform.billing.stripe``."""

    name = "stripe"

    def __init__(self, settings: dict[str, Any] | None = None, http_post: HttpPost | None = None):
        super().__init__(settings)
        self.http_post = http_post or _urllib_post

    def _post(self, path: str, form: dict[str, Any]) -> dict[str, Any]:
        key = str(self.settings.get("secret_key") or "")
        if not key:
            raise BillingError("platform.billing.stripe.secret_key is not set")
        url = f"{str(self.setting('api_base')).rstrip('/')}{path}"
        return self.http_post(url, form, {"Authorization": f"Bearer {key}"}, float(self.setting("timeout_seconds")))

    def checkout_url(
        self, tenant_id: str, plan: str, email: str | None, success_url: str, cancel_url: str, seats: int = 1
    ) -> str:
        price = (self.settings.get("prices") or {}).get(plan)
        if not price:
            raise BillingError(f"no Stripe price configured for plan '{plan}' (platform.billing.stripe.prices)")
        form: dict[str, Any] = {}
        _flatten("", self.settings.get("checkout") or {}, form)
        form.update(
            {
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
        )
        if email:
            form["customer_email"] = email
        url = self._post("/checkout/sessions", form).get("url")
        if not url:
            raise BillingError("Stripe did not return a checkout URL")
        return str(url)

    def portal_url(self, customer_id: str, return_url: str) -> str:
        return str(self._post("/billing_portal/sessions", {"customer": customer_id, "return_url": return_url})["url"])

    def report_usage(self, customer_id: str, metric: str, quantity: float, identifier: str) -> bool:
        meter = (self.settings.get("meters") or {}).get(metric)
        if not meter:
            return False  # metric not sold through Stripe; it stays on the offline invoice
        self._post(
            "/billing/meter_events",
            {
                "event_name": meter,
                "identifier": identifier,
                "payload[stripe_customer_id]": customer_id,
                "payload[value]": f"{quantity:g}",
            },
        )
        return True

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
        if abs((now or time.time()) - timestamp) > float(self.setting("signature_tolerance_seconds")):
            raise BillingError("webhook timestamp outside tolerance")
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
            raise BillingError("webhook signature does not match")

    def parse_webhook(self, payload: bytes, headers: dict[str, str]) -> list[BillingEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        self.verify_signature(payload, lowered.get("stripe-signature", ""))
        event = json.loads(payload)
        etype = str(event.get("type", ""))
        rule = (self.settings.get("events") or {}).get(etype)
        if not rule:
            return []  # acknowledged and ignored
        obj = (event.get("data") or {}).get("object") or {}
        meta = obj.get("metadata") or {}

        status = rule.get("status")
        if status == "from_subscription":
            status = (self.settings.get("status_map") or {}).get(
                str(obj.get("status")), self.settings.get("default_status")
            )
        # tenant_from "customer": no id here; the service resolves it from the stored customer id.
        tenant_id = {
            "client_reference_id": obj.get("client_reference_id") or meta.get("tenant_id"),
            "metadata": meta.get("tenant_id"),
        }.get(str(rule.get("tenant_from", "metadata")))
        plan = meta.get("plan") if rule.get("plan_from") == "metadata" else None
        return [
            BillingEvent(
                id=str(event.get("id")),
                tenant_id=tenant_id,
                customer_id=obj.get("customer"),
                plan=plan,
                status=status,
                raw_type=etype,
                data=obj,
            )
        ]


_PROVIDERS: dict[str, type[BillingProvider]] = {"manual": ManualBilling, "stripe": StripeBilling}


def register_billing_provider(name: str, cls: type[BillingProvider]) -> None:
    _PROVIDERS[name] = cls


def billing_provider(billing_settings: dict[str, Any] | None) -> BillingProvider:
    """Build the provider named by ``platform.billing.provider`` with its own settings block."""
    for name, cls in discover(BILLING_GROUP).items():
        _PROVIDERS.setdefault(name, cls)
    cfg = billing_settings or {}
    provider = cfg.get("provider")
    if not provider:
        raise ValueError("platform.billing.provider is not set")
    name = str(provider)
    if name not in _PROVIDERS:
        raise ValueError(f"unknown billing provider '{name}'. Available: {', '.join(sorted(_PROVIDERS))}")
    return _PROVIDERS[name](cfg.get(name) or {})
