"""
Configurable email delivery for the platform.

Supports multiple providers (SMTP, SendGrid, Mailgun, Postmark, SES, etc.) via a
pluggable interface. All configuration is in settings.yaml; no hard-coded templates.
Templates are stored as versioned files alongside legal documents.
"""

from __future__ import annotations

import abc
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import yaml

from financial_model.utils.plugins import discover


@dataclass(frozen=True)
class EmailTemplate:
    """A versioned email template with subject and body (text + HTML)."""

    key: str                    # e.g., "verify_email", "password_reset", "invite_member"
    name: str                   # display name
    version: str
    subject: str                # Jinja2-like template
    text_body: str              # plain text template
    html_body: str | None       # optional HTML template
    required_vars: list[str]    # variables that must be provided
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, key: str, name: str, subject: str, text_body: str,
               html_body: str | None = None, required_vars: list[str] | None = None,
               version: str | None = None, metadata: dict[str, Any] | None = None) -> EmailTemplate:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).timestamp()
        if version is None:
            version = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return cls(
            key=key,
            name=name,
            version=version,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            required_vars=required_vars or [],
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

    def render(self, variables: dict[str, Any]) -> tuple[str, str, str | None]:
        """Render template with variables. Returns (subject, text, html)."""
        missing = [v for v in self.required_vars if v not in variables]
        if missing:
            raise ValueError(f"Missing required template variables: {missing}")

        def render_template(template: str) -> str:
            result = template
            for key, value in variables.items():
                result = result.replace(f"{{{{{key}}}}}", str(value))
            return result

        return (
            render_template(self.subject),
            render_template(self.text_body),
            render_template(self.html_body) if self.html_body else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "subject": self.subject,
            "text_body": self.text_body,
            "html_body": self.html_body,
            "required_vars": self.required_vars,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class EmailProvider(abc.ABC):
    """Abstract base for email delivery providers."""

    name: str = "base"

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or {}

    @abc.abstractmethod
    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None,
             from_email: str | None = None, from_name: str | None = None) -> bool:
        """Send an email. Return True on success."""
        pass

    def setting(self, key: str) -> Any:
        if key not in self.settings:
            raise ValueError(f"email.{self.name}.{key} is not set")
        return self.settings[key]


class SMTPEmailProvider(EmailProvider):
    """SMTP email delivery (works with any SMTP server)."""

    name = "smtp"

    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None,
             from_email: str | None = None, from_name: str | None = None) -> bool:
        host = str(self.setting("host"))
        port = int(self.setting("port"))
        username = self.settings.get("username")
        password = self.settings.get("password")
        use_tls = bool(self.settings.get("use_tls", True))
        use_ssl = bool(self.settings.get("use_ssl", False))
        timeout = float(self.settings.get("timeout", 30))

        from_email = from_email or str(self.setting("from_email"))
        from_name = from_name or self.settings.get("from_name", "")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        context = ssl.create_default_context()

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                    if username and password:
                        server.login(username, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    if use_tls:
                        server.starttls(context=context)
                    if username and password:
                        server.login(username, password)
                    server.send_message(msg)
            return True
        except Exception as e:
            # Log error but don't raise - caller decides
            print(f"SMTP send failed: {e}")
            return False


class SendGridEmailProvider(EmailProvider):
    """SendGrid API email delivery."""

    name = "sendgrid"

    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None,
             from_email: str | None = None, from_name: str | None = None) -> bool:
        api_key = str(self.setting("api_key"))
        from_email = from_email or str(self.setting("from_email"))
        from_name = from_name or self.settings.get("from_name", "")

        payload = {
            "personalizations": [{
                "to": [{"email": to}],
                "subject": subject,
            }],
            "from": {"email": from_email, "name": from_name} if from_name else {"email": from_email},
            "content": [
                {"type": "text/plain", "value": text_body},
            ],
        }
        if html_body:
            payload["content"].append({"type": "text/html", "value": html_body})

        return self._post("/v3/mail/send", payload)

    def _post(self, path: str, payload: dict[str, Any]) -> bool:
        api_key = str(self.setting("api_key"))
        url = f"https://api.sendgrid.com{path}"
        data = __import__("json").dumps(payload).encode()

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            print(f"SendGrid error {e.code}: {e.read().decode()[:200]}")
            return False
        except Exception as e:
            print(f"SendGrid send failed: {e}")
            return False


class MailgunEmailProvider(EmailProvider):
    """Mailgun API email delivery."""

    name = "mailgun"

    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None,
             from_email: str | None = None, from_name: str | None = None) -> bool:
        api_key = str(self.setting("api_key"))
        domain = str(self.setting("domain"))
        from_email = from_email or str(self.setting("from_email"))

        form = {
            "from": f"{from_name} <{from_email}>" if from_name else from_email,
            "to": to,
            "subject": subject,
            "text": text_body,
        }
        if html_body:
            form["html"] = html_body

        url = f"https://api.mailgun.net/v3/{domain}/messages"
        data = urllib.parse.urlencode(form).encode()

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Basic {__import__('base64').b64encode(f'api:{api_key}'.encode()).decode()}"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            print(f"Mailgun send failed: {e}")
            return False


class ConsoleEmailProvider(EmailProvider):
    """Development provider that prints emails to console."""

    name = "console"

    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None,
             from_email: str | None = None, from_name: str | None = None) -> bool:
        print(f"\n{'='*60}")
        print(f"EMAIL (console provider)")
        print(f"To: {to}")
        print(f"From: {from_name or ''} <{from_email or 'noreply@localhost'}>")
        print(f"Subject: {subject}")
        print(f"{'-'*60}")
        print(text_body)
        if html_body:
            print(f"{'-'*60}")
            print(html_body)
        print(f"{'='*60}\n")
        return True


class EmailTemplateStore:
    """File-based storage for email templates."""

    BUILTIN_TEMPLATES = {
        "verify_email": {
            "name": "Email Verification",
            "subject": "Verify your email for {{platform_name}}",
            "required_vars": ["verification_url", "platform_name", "user_name"],
            "text_body": """Hi {{user_name}},

Welcome to {{platform_name}}! Please verify your email address by clicking the link below:

{{verification_url}}

This link expires in 24 hours.

If you didn't create an account, you can safely ignore this email.

Thanks,
The {{platform_name}} Team
""",
            "html_body": """<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 30px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">{{platform_name}}</h1>
    </div>
    <div style="background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
        <p>Hi {{user_name}},</p>
        <p>Welcome to <strong>{{platform_name}}</strong>! Please verify your email address by clicking the button below:</p>
        <p style="text-align: center; margin: 30px 0;">
            <a href="{{verification_url}}" style="background: #2563eb; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">Verify Email Address</a>
        </p>
        <p style="color: #64748b; font-size: 14px;">This link expires in 24 hours.</p>
        <p>If you didn't create an account, you can safely ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #64748b; font-size: 13px;">Thanks,<br>The {{platform_name}} Team</p>
    </div>
</body>
</html>""",
        },
        "password_reset": {
            "name": "Password Reset",
            "subject": "Reset your {{platform_name}} password",
            "required_vars": ["reset_url", "platform_name", "user_name"],
            "text_body": """Hi {{user_name}},

You requested a password reset for your {{platform_name}} account. Click the link below to set a new password:

{{reset_url}}

This link expires in 1 hour.

If you didn't request this, please ignore this email or contact support if concerned.

Thanks,
The {{platform_name}} Team
""",
            "html_body": """<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #dc2626, #b91c1c); padding: 30px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">{{platform_name}}</h1>
    </div>
    <div style="background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
        <p>Hi {{user_name}},</p>
        <p>You requested a password reset for your <strong>{{platform_name}}</strong> account. Click the button below to set a new password:</p>
        <p style="text-align: center; margin: 30px 0;">
            <a href="{{reset_url}}" style="background: #dc2626; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">Reset Password</a>
        </p>
        <p style="color: #64748b; font-size: 14px;">This link expires in 1 hour.</p>
        <p>If you didn't request this, please ignore this email or contact support if concerned.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #64748b; font-size: 13px;">Thanks,<br>The {{platform_name}} Team</p>
    </div>
</body>
</html>""",
        },
        "invite_member": {
            "name": "Team Invitation",
            "subject": "You're invited to join {{account_name}} on {{platform_name}}",
            "required_vars": ["invite_url", "platform_name", "account_name", "inviter_name", "role"],
            "text_body": """Hi there,

{{inviter_name}} invited you to join "{{account_name}}" on {{platform_name}} as a {{role}}.

Accept the invitation by clicking the link below:

{{invite_url}}

This invitation expires in 7 days.

Thanks,
The {{platform_name}} Team
""",
            "html_body": """<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #16a34a, #15803d); padding: 30px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">{{platform_name}}</h1>
    </div>
    <div style="background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
        <p><strong>{{inviter_name}}</strong> invited you to join <strong>"{{account_name}}"</strong> on {{platform_name}} as a <strong>{{role}}</strong>.</p>
        <p style="text-align: center; margin: 30px 0;">
            <a href="{{invite_url}}" style="background: #16a34a; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">Accept Invitation</a>
        </p>
        <p style="color: #64748b; font-size: 14px;">This invitation expires in 7 days.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #64748b; font-size: 13px;">Thanks,<br>The {{platform_name}} Team</p>
    </div>
</body>
</html>""",
        },
        "billing_receipt": {
            "name": "Billing Receipt",
            "subject": "Your {{platform_name}} receipt - {{amount}} {{currency}}",
            "required_vars": ["platform_name", "amount", "currency", "plan_name", "period", "receipt_url"],
            "text_body": """Hi there,

Thank you for your payment!

Plan: {{plan_name}}
Amount: {{amount}} {{currency}}
Period: {{period}}

View your receipt: {{receipt_url}}

Thanks,
The {{platform_name}} Team
""",
            "html_body": """<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #0891b2, #0e7490); padding: 30px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">{{platform_name}}</h1>
    </div>
    <div style="background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
        <p>Thank you for your payment!</p>
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
            <tr><td style="padding: 8px; color: #64748b;">Plan</td><td style="padding: 8px; font-weight: 600;">{{plan_name}}</td></tr>
            <tr><td style="padding: 8px; color: #64748b;">Amount</td><td style="padding: 8px; font-weight: 600;">{{amount}} {{currency}}</td></tr>
            <tr><td style="padding: 8px; color: #64748b;">Period</td><td style="padding: 8px; font-weight: 600;">{{period}}</td></tr>
        </table>
        <p style="text-align: center; margin: 30px 0;">
            <a href="{{receipt_url}}" style="background: #0891b2; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">View Receipt</a>
        </p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #64748b; font-size: 13px;">Thanks,<br>The {{platform_name}} Team</p>
    </div>
</body>
</html>""",
        },
        "usage_alert": {
            "name": "Usage Alert",
            "subject": "Usage alert: {{metric}} at {{percent}}% of limit on {{platform_name}}",
            "required_vars": ["platform_name", "metric", "used", "limit", "percent", "account_name"],
            "text_body": """Hi there,

Your account "{{account_name}}" on {{platform_name}} has reached {{percent}}% of its {{metric}} limit.

Used: {{used}} / {{limit}}

Consider upgrading your plan for higher limits.

Thanks,
The {{platform_name}} Team
""",
            "html_body": """<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #ea580c, #c2410c); padding: 30px; border-radius: 12px 12px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">{{platform_name}}</h1>
    </div>
    <div style="background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
        <p>Your account <strong>"{{account_name}}"</strong> has reached <strong>{{percent}}%</strong> of its <strong>{{metric}}</strong> limit.</p>
        <div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                <span>Used</span><span style="font-weight: 600;">{{used}}</span>
            </div>
            <div style="display: flex; justify-content: space-between;">
                <span>Limit</span><span style="font-weight: 600;">{{limit}}</span>
            </div>
            <div style="height: 8px; background: #e2e8f0; border-radius: 4px; margin-top: 10px; overflow: hidden;">
                <div style="height: 100%; width: {{percent}}%; background: #ea580c; transition: width 0.3s;"></div>
            </div>
        </div>
        <p>Consider upgrading your plan for higher limits.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="color: #64748b; font-size: 13px;">Thanks,<br>The {{platform_name}} Team</p>
    </div>
</body>
</html>""",
        },
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, EmailTemplate] = {}
        self._load_all()

    def _load_all(self) -> None:
        for file in self.path.glob("*.yaml"):
            key = file.stem
            try:
                with file.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    self._cache[key] = EmailTemplate(**data)
            except Exception:
                pass

    def _save(self, key: str) -> None:
        file = self.path / f"{key}.yaml"
        template = self._cache.get(key)
        if template:
            with file.open("w", encoding="utf-8") as f:
                yaml.dump(template.to_dict(), f, sort_keys=False, default_flow_style=False)

    def ensure_builtins(self) -> list[EmailTemplate]:
        created = []
        for key, spec in self.BUILTIN_TEMPLATES.items():
            if key not in self._cache:
                template = EmailTemplate.create(
                    key=key,
                    name=spec["name"],
                    subject=spec["subject"],
                    text_body=spec["text_body"],
                    html_body=spec.get("html_body"),
                    required_vars=spec["required_vars"],
                )
                self._cache[key] = template
                self._save(key)
                created.append(template)
        return created

    def get(self, key: str) -> EmailTemplate | None:
        return self._cache.get(key)

    def get_all(self) -> dict[str, EmailTemplate]:
        return dict(self._cache)

    def create_or_update(self, key: str, name: str, subject: str, text_body: str,
                         html_body: str | None = None, required_vars: list[str] | None = None,
                         version: str | None = None) -> EmailTemplate:
        existing = self._cache.get(key)
        template = EmailTemplate.create(
            key=key,
            name=name,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            required_vars=required_vars or [],
            version=version,
        )
        self._cache[key] = template
        self._save(key)
        return template


_PROVIDERS: dict[str, type[EmailProvider]] = {
    "smtp": SMTPEmailProvider,
    "sendgrid": SendGridEmailProvider,
    "mailgun": MailgunEmailProvider,
    "console": ConsoleEmailProvider,
}


def register_email_provider(name: str, cls: type[EmailProvider]) -> None:
    _PROVIDERS[name] = cls


def email_provider(email_settings: dict[str, Any] | None) -> EmailProvider:
    """Build the email provider from settings."""
    for name, cls in discover("financial_model.email_providers").items():
        _PROVIDERS.setdefault(name, cls)

    cfg = email_settings or {}
    provider_name = cfg.get("provider", "console")

    if provider_name not in _PROVIDERS:
        raise ValueError(f"unknown email provider '{provider_name}'. Available: {', '.join(sorted(_PROVIDERS))}")

    provider_settings = cfg.get(provider_name) or {}
    # Merge top-level settings that aren't provider-specific
    for k, v in cfg.items():
        if k not in _PROVIDERS and k != "provider" and k not in provider_settings:
            provider_settings[k] = v

    return _PROVIDERS[provider_name](provider_settings)


class EmailService:
    """High-level email service combining provider + templates."""

    def __init__(self, provider: EmailProvider, templates: EmailTemplateStore,
                 default_from_email: str, default_from_name: str, platform_name: str):
        self.provider = provider
        self.templates = templates
        self.default_from_email = default_from_email
        self.default_from_name = default_from_name
        self.platform_name = platform_name

    def send_template(self, to: str, template_key: str, variables: dict[str, Any],
                      from_email: str | None = None, from_name: str | None = None) -> bool:
        """Send an email using a template."""
        template = self.templates.get(template_key)
        if not template:
            raise ValueError(f"Unknown email template: {template_key}")

        # Add platform defaults
        vars_ = {
            "platform_name": self.platform_name,
            **variables,
        }

        subject, text_body, html_body = template.render(vars_)

        return self.provider.send(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            from_email=from_email or self.default_from_email,
            from_name=from_name or self.default_from_name,
        )

    def send_verification(self, to: str, user_name: str, verification_url: str) -> bool:
        return self.send_template(to, "verify_email", {
            "user_name": user_name,
            "verification_url": verification_url,
        })

    def send_password_reset(self, to: str, user_name: str, reset_url: str) -> bool:
        return self.send_template(to, "password_reset", {
            "user_name": user_name,
            "reset_url": reset_url,
        })

    def send_invite(self, to: str, account_name: str, inviter_name: str, role: str, invite_url: str) -> bool:
        return self.send_template(to, "invite_member", {
            "account_name": account_name,
            "inviter_name": inviter_name,
            "role": role,
            "invite_url": invite_url,
        })

    def send_billing_receipt(self, to: str, amount: str, currency: str, plan_name: str,
                             period: str, receipt_url: str) -> bool:
        return self.send_template(to, "billing_receipt", {
            "amount": amount,
            "currency": currency,
            "plan_name": plan_name,
            "period": period,
            "receipt_url": receipt_url,
        })

    def send_usage_alert(self, to: str, account_name: str, metric: str, used: float,
                         limit: float, percent: float) -> bool:
        return self.send_template(to, "usage_alert", {
            "account_name": account_name,
            "metric": metric,
            "used": used,
            "limit": limit,
            "percent": percent,
        })