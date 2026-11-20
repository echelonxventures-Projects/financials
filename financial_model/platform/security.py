"""
Credentials: password hashes, API keys and signed session tokens (standard library only).

* Passwords: PBKDF2-HMAC-SHA256 with a per-password salt, encoded
  ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` so the work factor can be raised later.
* API keys: ``fm_<prefix>_<secret>``. Only a SHA-256 digest is stored; the prefix
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
from typing import Any

KEY_PREFIX = "fm"


def hash_password(password: str, iterations: int = 600_000) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
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


def new_api_key() -> tuple[str, str, str]:
    """Return ``(plain_key, prefix, digest)``; store prefix + digest, show the plain key once."""
    prefix = secrets.token_hex(4)
    key = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(24)}"
    return key, prefix, digest_key(key)


def digest_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str | None:
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1]:
        return None
    return parts[1]


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
