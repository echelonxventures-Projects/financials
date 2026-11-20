"""
Commercial platform: multi-tenant accounts, plans, metering and billing.

Turns the self-hosted API into a hosted product. Individuals sign up themselves
(B2C), organisations get members, roles and seats (B2B), and every model endpoint
is metered per tenant so the same deployment can be sold as a PaaS API. Everything
here uses the standard library; the HTTP layer needs the ``api`` extra.

Enable it with ``platform.enabled: true`` in the settings file (or
``FIN_PLATFORM_ENABLED=1``). See docs/commercial.md.
"""

from __future__ import annotations

from financial_model.platform.billing import BillingEvent, BillingProvider, register_billing_provider
from financial_model.platform.plans import DEFAULT_PLANS, Plan, load_plans
from financial_model.platform.service import PlatformService, Principal
from financial_model.platform.store import SQLiteStore, Store, open_store, register_store

__all__ = [
    "DEFAULT_PLANS",
    "BillingEvent",
    "BillingProvider",
    "Plan",
    "PlatformService",
    "Principal",
    "SQLiteStore",
    "Store",
    "load_plans",
    "open_store",
    "register_billing_provider",
    "register_store",
]
