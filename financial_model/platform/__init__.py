"""
Commercial platform: multi-tenant accounts, plans, metering and billing.

Turns the self-hosted API into a hosted product. Individuals sign up themselves
(B2C), organisations get members, roles and seats (B2B), and every model endpoint
is metered per tenant so the same deployment can be sold as a PaaS API. Prices,
plans, pay-to-use rates, quotas, roles and endpoint rules are data (catalog.yaml,
settings.yaml), overridable from the settings file. Everything here uses the
standard library; the HTTP layer needs the ``api`` extra.

Enable it with ``platform.enabled: true`` in the settings file (or
``FIN_PLATFORM_ENABLED=1``). See docs/commercial.md.
"""

from __future__ import annotations

from financial_model.platform.billing import BillingEvent, BillingProvider, register_billing_provider
from financial_model.platform.catalog import Catalog, CatalogError, MeterRule, Plan, builtin_settings
from financial_model.platform.email import EmailProvider, EmailService, EmailTemplate, EmailTemplateStore, email_provider, register_email_provider
from financial_model.platform.legal import LegalDocument, LegalStore, validate_legal_content
from financial_model.platform.sso import SSOProvider, SSOService, SSOStore, SSOUserInfo, SSOProviderBase, build_sso_provider, register_sso_provider_type
from financial_model.platform.service import PlatformService, Principal
from financial_model.platform.store import SQLiteStore, Store, open_store, register_store

__all__ = [
    "BillingEvent",
    "BillingProvider",
    "Catalog",
    "CatalogError",
    "MeterRule",
    "Plan",
    "PlatformService",
    "Principal",
    "SQLiteStore",
    "Store",
    "builtin_settings",
    "open_store",
    "register_billing_provider",
    "register_store",
    "EmailProvider",
    "EmailService",
    "EmailTemplate",
    "EmailTemplateStore",
    "email_provider",
    "register_email_provider",
    "LegalDocument",
    "LegalStore",
    "validate_legal_content",
    "SSOProvider",
    "SSOService",
    "SSOStore",
    "SSOUserInfo",
    "SSOProviderBase",
    "build_sso_provider",
    "register_sso_provider_type",
]
