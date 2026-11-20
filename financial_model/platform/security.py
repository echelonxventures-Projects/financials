"""
Credentials: password hashes, API keys and signed session tokens (standard library only).

* Passwords: PBKDF2-HMAC-SHA256 with a per-password salt, encoded
  ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` so the work factor can be raised later.
* API keys: ``<api_key_prefix>_<id>_<secret>``. Only a SHA-256 digest is stored; the prefix
  finds the row, the digest is compared in constant time. The plain key is shown once.
* Session tokens: ``<base64url(json)>.<base64url(hmac)>`` signed with the platform
  secret, carrying user, tenant and expiry. Stateless, so any replica can verify them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


def hash_password(password: str, iterations: int, min_length: int = 1) -> str:
    """Hash ``password``; the work factor and length rule come from the platform settings."""
    if len(password) < min_length:
        raise ValueError(f"password must be at least {min_length} characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, iterations, salt, digest = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


@dataclass(frozen=True)
class KeyFormat:
    """API keys ``<brand>_<lookup id>_<secret>``; ``brand`` is the ``api_key_prefix`` setting."""

    brand: str

    def new(self) -> tuple[str, str, str]:
        """Return ``(plain_key, lookup_id, digest)``; store the id + digest, show the plain key once."""
        lookup = secrets.token_hex(4)
        key = f"{self.brand}_{lookup}_{secrets.token_urlsafe(24)}"
        return key, lookup, self.digest(key)

    @staticmethod
    def digest(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def prefix_of(self, key: str) -> str | None:
        head, sep, rest = key.partition(f"{self.brand}_")
        if head or not sep:
            return None
        lookup, sep, secret = rest.partition("_")
        return lookup if sep and lookup and secret else None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(claims: dict[str, Any], secret: str, ttl_seconds: int) -> tuple[str, int]:
    expires = int(time.time()) + int(ttl_seconds)
    body = _b64(json.dumps({**claims, "exp": expires}, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", expires


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or int(claims.get("exp", 0)) < time.time():
        return None
    return claims
