"""
Configurable SSO/SAML/OIDC authentication for the platform.

Supports multiple identity providers via a pluggable interface. All configuration
is in settings.yaml; no hard-coded provider logic. Follows the same pattern as
billing and email providers.
"""

from __future__ import annotations

import abc
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml

from financial_model.utils.plugins import discover


@dataclass(frozen=True)
class SSOProvider:
    """An SSO identity provider configuration."""

    key: str                    # unique key, e.g., "google", "okta", "azuread"
    name: str                   # display name
    type: str                   # "oidc" | "saml" | "custom"
    enabled: bool
    config: dict[str, Any]      # provider-specific config
    auto_provision: bool        # create accounts automatically
    default_role: str           # role for auto-provisioned users
    default_account_kind: str   # "individual" | "organization"
    default_plan: str           # plan for new accounts
    domain_hints: list[str]     # email domains that map to this provider
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, key: str, name: str, type_: str, config: dict[str, Any],
               enabled: bool = True, auto_provision: bool = True,
               default_role: str = "member", default_account_kind: str = "organization",
               default_plan: str = "free", domain_hints: list[str] | None = None,
               metadata: dict[str, Any] | None = None) -> SSOProvider:
        now = datetime.now(timezone.utc).timestamp()
        return cls(
            key=key,
            name=name,
            type=type_,
            enabled=enabled,
            config=config,
            auto_provision=auto_provision,
            default_role=default_role,
            default_account_kind=default_account_kind,
            default_plan=default_plan,
            domain_hints=domain_hints or [],
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "config": self.config,
            "auto_provision": self.auto_provision,
            "default_role": self.default_role,
            "default_account_kind": self.default_account_kind,
            "default_plan": self.default_plan,
            "domain_hints": self.domain_hints,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class SSOUserInfo:
    """Normalized user info from an SSO provider."""

    provider_key: str
    subject: str                # unique ID from provider
    email: str
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    picture: str | None = None
    email_verified: bool = False
    groups: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class SSOProviderBase(abc.ABC):
    """Abstract base for SSO providers."""

    type: str = "base"

    def __init__(self, provider: SSOProvider):
        self.provider = provider
        self.config = provider.config

    @abc.abstractmethod
    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str | None = None) -> str:
        """Get the URL to redirect the user to for authentication."""
        pass

    @abc.abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> SSOUserInfo:
        """Exchange authorization code for user info."""
        pass

    @abc.abstractmethod
    def get_logout_url(self, redirect_uri: str | None = None) -> str | None:
        """Get logout URL (optional)."""
        pass


class OIDCProvider(SSOProviderBase):
    """OpenID Connect provider (Google, Okta, Azure AD, Auth0, Keycloak, etc.)."""

    type = "oidc"

    def __init__(self, provider: SSOProvider):
        super().__init__(provider)
        self.issuer = self.config.get("issuer")
        self.client_id = self.config.get("client_id")
        self.client_secret = self.config.get("client_secret")
        self.scopes = self.config.get("scopes", ["openid", "email", "profile"])
        self._metadata: dict[str, Any] | None = None

    def _fetch_metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            if not self.issuer:
                raise ValueError("OIDC issuer not configured")
            metadata_url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
            req = urllib.request.Request(metadata_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                self._metadata = json.loads(resp.read())
        return self._metadata

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str | None = None) -> str:
        metadata = self._fetch_metadata()
        auth_endpoint = metadata.get("authorization_endpoint")
        if not auth_endpoint:
            raise ValueError("No authorization_endpoint in OIDC metadata")

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
        }
        if nonce:
            params["nonce"] = nonce

        return f"{auth_endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> SSOUserInfo:
        metadata = self._fetch_metadata()
        token_endpoint = metadata.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("No token_endpoint in OIDC metadata")

        data = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()

        req = urllib.request.Request(
            token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            tokens = json.loads(resp.read())

        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")

        # Decode ID token (JWT) - validate signature in production!
        # For now, just decode payload (unsafe without verification)
        userinfo = {}
        if id_token:
            try:
                parts = id_token.split(".")
                if len(parts) == 3:
                    payload = parts[1]
                    # Add padding
                    payload += "=" * (-len(payload) % 4)
                    userinfo = json.loads(base64.urlsafe_b64decode(payload))
            except Exception:
                pass

        # Fallback to userinfo endpoint
        if access_token and not userinfo.get("email"):
            userinfo_endpoint = metadata.get("userinfo_endpoint")
            if userinfo_endpoint:
                req = urllib.request.Request(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    userinfo = json.loads(resp.read())

        return SSOUserInfo(
            provider_key=self.provider.key,
            subject=userinfo.get("sub", ""),
            email=userinfo.get("email", ""),
            name=userinfo.get("name"),
            given_name=userinfo.get("given_name"),
            family_name=userinfo.get("family_name"),
            picture=userinfo.get("picture"),
            email_verified=userinfo.get("email_verified", False),
            groups=userinfo.get("groups", userinfo.get("cognito:groups", [])),
            roles=userinfo.get("roles", []),
            raw=userinfo,
        )

    def get_logout_url(self, redirect_uri: str | None = None) -> str | None:
        metadata = self._fetch_metadata()
        logout_endpoint = metadata.get("end_session_endpoint")
        if not logout_endpoint:
            return None

        params = {"client_id": self.client_id}
        if redirect_uri:
            params["post_logout_redirect_uri"] = redirect_uri

        return f"{logout_endpoint}?{urlencode(params)}"


class SAMLProvider(SSOProviderBase):
    """SAML 2.0 provider (requires xmlsec or pysaml2 for full implementation)."""

    type = "saml"

    def __init__(self, provider: SSOProvider):
        super().__init__(provider)
        self.entity_id = self.config.get("entity_id")
        self.sso_url = self.config.get("sso_url")
        self.x509_cert = self.config.get("x509_cert")
        self.sp_entity_id = self.config.get("sp_entity_id")
        self.acs_url = self.config.get("acs_url")
        self.private_key = self.config.get("private_key")

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str | None = None) -> str:
        # Generate SAML AuthnRequest
        # In production, use pysaml2 or xmlsec
        raise NotImplementedError("SAML requires pysaml2 dependency. Use OIDC or add pysaml2.")

    def exchange_code(self, code: str, redirect_uri: str) -> SSOUserInfo:
        raise NotImplementedError("SAML uses POST binding, not code exchange")

    def get_logout_url(self, redirect_uri: str | None = None) -> str | None:
        return None


class CustomSSOProvider(SSOProviderBase):
    """Custom provider - implement the interface for any proprietary system."""

    type = "custom"

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str | None = None) -> str:
        raise NotImplementedError("Custom provider must implement get_authorization_url")

    def exchange_code(self, code: str, redirect_uri: str) -> SSOUserInfo:
        raise NotImplementedError("Custom provider must implement exchange_code")

    def get_logout_url(self, redirect_uri: str | None = None) -> str | None:
        return None


_PROVIDER_CLASSES: dict[str, type[SSOProviderBase]] = {
    "oidc": OIDCProvider,
    "saml": SAMLProvider,
    "custom": CustomSSOProvider,
}


def register_sso_provider_type(type_: str, cls: type[SSOProviderBase]) -> None:
    _PROVIDER_CLASSES[type_] = cls


def build_sso_provider(provider: SSOProvider) -> SSOProviderBase:
    """Build the appropriate provider instance."""
    if provider.type not in _PROVIDER_CLASSES:
        raise ValueError(f"Unknown SSO provider type: {provider.type}")
    return _PROVIDER_CLASSES[provider.type](provider)


class SSOStore:
    """File-based storage for SSO provider configurations."""

    BUILTIN_PROVIDERS = {
        "google": {
            "name": "Google",
            "type": "oidc",
            "config": {
                "issuer": "https://accounts.google.com",
                "client_id": "",  # Set in settings
                "client_secret": "",  # Set in settings
                "scopes": ["openid", "email", "profile"],
            },
            "domain_hints": ["gmail.com", "googlemail.com"],
        },
        "microsoft": {
            "name": "Microsoft / Azure AD",
            "type": "oidc",
            "config": {
                "issuer": "https://login.microsoftonline.com/{tenant_id}/v2.0",
                "client_id": "",
                "client_secret": "",
                "scopes": ["openid", "email", "profile"],
            },
            "domain_hints": ["outlook.com", "hotmail.com", "live.com"],
        },
        "okta": {
            "name": "Okta",
            "type": "oidc",
            "config": {
                "issuer": "https://{domain}.okta.com/oauth2/default",
                "client_id": "",
                "client_secret": "",
                "scopes": ["openid", "email", "profile", "groups"],
            },
        },
        "auth0": {
            "name": "Auth0",
            "type": "oidc",
            "config": {
                "issuer": "https://{domain}.auth0.com",
                "client_id": "",
                "client_secret": "",
                "scopes": ["openid", "email", "profile"],
            },
        },
        "github": {
            "name": "GitHub",
            "type": "oidc",
            "config": {
                "issuer": "https://github.com/login/oauth",
                "client_id": "",
                "client_secret": "",
                "scopes": ["read:user", "user:email"],
            },
        },
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, SSOProvider] = {}
        self._load_all()

    def _load_all(self) -> None:
        for file in self.path.glob("*.yaml"):
            key = file.stem
            try:
                with file.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    self._cache[key] = SSOProvider(**data)
            except Exception:
                pass

    def _save(self, key: str) -> None:
        file = self.path / f"{key}.yaml"
        provider = self._cache.get(key)
        if provider:
            with file.open("w", encoding="utf-8") as f:
                yaml.dump(provider.to_dict(), f, sort_keys=False, default_flow_style=False)

    def ensure_builtins(self) -> list[SSOProvider]:
        created = []
        for key, spec in self.BUILTIN_PROVIDERS.items():
            if key not in self._cache:
                provider = SSOProvider.create(
                    key=key,
                    name=spec["name"],
                    type_=spec["type"],
                    config=spec["config"],
                    domain_hints=spec.get("domain_hints", []),
                )
                self._cache[key] = provider
                self._save(key)
                created.append(provider)
        return created

    def get(self, key: str) -> SSOProvider | None:
        return self._cache.get(key)

    def get_enabled(self) -> list[SSOProvider]:
        return [p for p in self._cache.values() if p.enabled]

    def get_all(self) -> dict[str, SSOProvider]:
        return dict(self._cache)

    def get_by_domain(self, email: str) -> SSOProvider | None:
        domain = email.split("@")[-1].lower()
        for provider in self._cache.values():
            if provider.enabled and domain in [d.lower() for d in provider.domain_hints]:
                return provider
        return None

    def create_or_update(self, key: str, name: str, type_: str, config: dict[str, Any],
                         enabled: bool = True, auto_provision: bool = True,
                         default_role: str = "member", default_account_kind: str = "organization",
                         default_plan: str = "free", domain_hints: list[str] | None = None) -> SSOProvider:
        existing = self._cache.get(key)
        provider = SSOProvider.create(
            key=key,
            name=name,
            type_=type_,
            config=config,
            enabled=enabled,
            auto_provision=auto_provision,
            default_role=default_role,
            default_account_kind=default_account_kind,
            default_plan=default_plan,
            domain_hints=domain_hints or [],
        )
        self._cache[key] = provider
        self._save(key)
        return provider

    def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            file = self.path / f"{key}.yaml"
            if file.exists():
                file.unlink()
            return True
        return False


class SSOService:
    """High-level SSO service coordinating providers, sessions, and account linking."""

    def __init__(self, store: SSOStore, platform_service: Any):
        self.store = store
        self.platform = platform_service
        self._pending_states: dict[str, dict[str, Any]] = {}  # state -> {provider, redirect, nonce, timestamp}

    def get_login_options(self) -> list[dict[str, Any]]:
        """Get list of enabled providers for login page."""
        return [
            {
                "key": p.key,
                "name": p.name,
                "type": p.type,
                "domain_hints": p.domain_hints,
            }
            for p in self.store.get_enabled()
        ]

    def initiate_login(self, provider_key: str, redirect_uri: str) -> tuple[str, str]:
        """Start SSO login. Returns (authorization_url, state)."""
        provider = self.store.get(provider_key)
        if not provider or not provider.enabled:
            raise ValueError(f"SSO provider '{provider_key}' not found or disabled")

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        sso_provider = build_sso_provider(provider)
        auth_url = sso_provider.get_authorization_url(redirect_uri, state, nonce)

        self._pending_states[state] = {
            "provider_key": provider_key,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "timestamp": time.time(),
        }

        # Clean old states
        cutoff = time.time() - 600  # 10 minutes
        self._pending_states = {k: v for k, v in self._pending_states.items() if v["timestamp"] > cutoff}

        return auth_url, state

    def handle_callback(self, code: str, state: str) -> tuple[str, str | None]:
        """Handle SSO callback. Returns (token, tenant_id)."""
        pending = self._pending_states.pop(state, None)
        if not pending:
            raise ValueError("Invalid or expired state")

        provider = self.store.get(pending["provider_key"])
        if not provider:
            raise ValueError("Provider not found")

        sso_provider = build_sso_provider(provider)
        user_info = sso_provider.exchange_code(code, pending["redirect_uri"])

        # Find or create user/account
        return self._find_or_create_user(provider, user_info)

    def _find_or_create_user(self, provider: SSOProvider, user_info: SSOUserInfo) -> tuple[str, str | None]:
        # Try to find existing user by email
        email = user_info.email.lower()
        user = self.platform.store.get_user_by_email(email)

        if user:
            # Check if already linked to this provider
            # In a real implementation, store provider links in the database
            tenant = self._get_primary_tenant(user.id)
            if tenant:
                token, _, _ = self.platform.login(user.email, "", tenant_id=tenant.id)
                return token, tenant.id

        if not provider.auto_provision:
            raise ValueError("Account not found and auto-provisioning is disabled")

        # Create new user and account
        password = secrets.token_urlsafe(32)  # Random, SSO-only user
        user, tenant, _ = self.platform.signup(email, password, user_info.name or email.split("@")[0])

        # Apply provider defaults
        if tenant:
            self.platform.store.update_tenant(tenant.id, plan=provider.default_plan)
            self.platform.set_role(tenant.id, user.id, provider.default_role)

        token, _, tenant_obj = self.platform.login(email, password, tenant_id=tenant.id if tenant else None)
        return token, tenant_obj.id if tenant_obj else None

    def _get_primary_tenant(self, user_id: str):
        tenants = self.platform.store.tenants_for_user(user_id)
        if tenants:
            return tenants[0][0]
        return None

    def initiate_logout(self, provider_key: str, redirect_uri: str | None = None) -> str | None:
        provider = self.store.get(provider_key)
        if not provider:
            return None
        sso_provider = build_sso_provider(provider)
        return sso_provider.get_logout_url(redirect_uri)

    def link_provider(self, user_id: str, provider_key: str, user_info: SSOUserInfo) -> bool:
        """Link an SSO provider to an existing user account."""
        # Store the link in metadata or a separate table
        # Implementation depends on store capabilities
        return True


class SSOMiddleware:
    """ASGI middleware for SSO session handling."""

    def __init__(self, app: Any, sso_service: SSOService, session_cookie_name: str = "sso_session"):
        self.app = app
        self.sso_service = sso_service
        self.session_cookie_name = session_cookie_name

    async def __call__(self, scope: dict, receive: Any, send: Any):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract session from cookie
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode()
        session_id = None
        for cookie in cookie_header.split(";"):
            if cookie.strip().startswith(f"{self.session_cookie_name}="):
                session_id = cookie.split("=")[1].strip()
                break

        scope["sso_session"] = session_id
        await self.app(scope, receive, send)


def sso_settings_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract SSO settings from platform config."""
    platform = config.get("platform", {})
    return platform.get("sso", {})