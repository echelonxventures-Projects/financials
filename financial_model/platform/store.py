"""
Persistence for tenants, users, memberships, API keys and usage.

The default store is SQLite from the standard library: one file, no server, works
on every OS and in a container with a mounted volume. ``database`` in the platform
settings is a URL: ``sqlite:///path/to/platform.db`` or ``sqlite:///:memory:``.
Other back ends (Postgres, DynamoDB...) plug in by implementing :class:`Store` and
registering a URL scheme with :func:`register_store` or the
``financial_model.platform_stores`` entry point.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from financial_model.utils.plugins import discover

STORE_GROUP = "financial_model.platform_stores"


@dataclass
class Tenant:
    id: str
    kind: str  # a catalogue account kind
    name: str
    plan: str  # a catalogue plan key
    status: str  # a catalogue account status
    billing_customer_id: str | None = None
    created_at: float = 0.0


@dataclass
class User:
    id: str
    email: str
    name: str
    password_hash: str | None
    created_at: float = 0.0


@dataclass
class ApiKey:
    id: str
    tenant_id: str
    user_id: str | None
    prefix: str
    digest: str
    name: str
    created_at: float
    revoked_at: float | None = None
    last_used_at: float | None = None


class Store:  # pragma: no cover - interface only
    """Storage interface used by :class:`~financial_model.platform.service.PlatformService`.

    Every method must be implemented by a back end; the bodies here only document the shape.
    """

    # tenants
    def add_tenant(self, tenant: Tenant) -> None:
        raise NotImplementedError

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        raise NotImplementedError

    def update_tenant(self, tenant_id: str, **fields: Any) -> None:
        raise NotImplementedError

    def find_tenant_by_customer(self, customer_id: str) -> Tenant | None:
        raise NotImplementedError

    def list_tenants(self) -> list[Tenant]:
        raise NotImplementedError

    # users
    def add_user(self, user: User) -> None:
        raise NotImplementedError

    def get_user(self, user_id: str) -> User | None:
        raise NotImplementedError

    def get_user_by_email(self, email: str) -> User | None:
        raise NotImplementedError

    # memberships
    def set_membership(self, tenant_id: str, user_id: str, role: str) -> None:
        raise NotImplementedError

    def remove_membership(self, tenant_id: str, user_id: str) -> None:
        raise NotImplementedError

    def get_role(self, tenant_id: str, user_id: str) -> str | None:
        raise NotImplementedError

    def members(self, tenant_id: str) -> list[tuple[User, str]]:
        raise NotImplementedError

    def tenants_for_user(self, user_id: str) -> list[tuple[Tenant, str]]:
        raise NotImplementedError

    # keys
    def add_key(self, key: ApiKey) -> None:
        raise NotImplementedError

    def get_key_by_prefix(self, prefix: str) -> ApiKey | None:
        raise NotImplementedError

    def list_keys(self, tenant_id: str) -> list[ApiKey]:
        raise NotImplementedError

    def revoke_key(self, tenant_id: str, key_id: str) -> bool:
        raise NotImplementedError

    def touch_key(self, key_id: str) -> None:
        raise NotImplementedError

    # usage + billing events
    def add_usage(self, tenant_id: str, period: str, metric: str, quantity: float) -> None:
        raise NotImplementedError

    def get_usage(self, tenant_id: str, period: str) -> dict[str, float]:
        raise NotImplementedError

    def mark_event(self, event_id: str) -> bool:
        raise NotImplementedError

    # failed sign-ins (login throttling)
    def add_login_failure(self, key: str, at: float, prune_before: float) -> None:
        """Record a failed sign-in for ``key``; failures older than ``prune_before`` may be dropped."""
        raise NotImplementedError

    def count_login_failures(self, key: str, since: float) -> tuple[int, float | None]:
        """``(count, oldest timestamp)`` of failures for ``key`` at or after ``since``."""
        raise NotImplementedError

    def clear_login_failures(self, key: str) -> None:
        raise NotImplementedError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, plan TEXT NOT NULL,
    status TEXT NOT NULL, billing_customer_id TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tenants_customer ON tenants(billing_customer_id);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    password_hash TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL, PRIMARY KEY (tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT,
    prefix TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, name TEXT NOT NULL,
    created_at REAL NOT NULL, revoked_at REAL, last_used_at REAL
);
CREATE TABLE IF NOT EXISTS usage (
    tenant_id TEXT NOT NULL, period TEXT NOT NULL, metric TEXT NOT NULL, quantity REAL NOT NULL,
    PRIMARY KEY (tenant_id, period, metric)
);
CREATE TABLE IF NOT EXISTS billing_events (id TEXT PRIMARY KEY, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_failures (key TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures(key, at);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class SQLiteStore(Store):
    """Thread-safe SQLite store (one connection guarded by a lock)."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(_SCHEMA)

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._db.execute(sql, params).fetchone()
            return row

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # tenants -------------------------------------------------------------
    def add_tenant(self, tenant: Tenant) -> None:
        self._exec(
            "INSERT INTO tenants (id, kind, name, plan, status, billing_customer_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                tenant.id,
                tenant.kind,
                tenant.name,
                tenant.plan,
                tenant.status,
                tenant.billing_customer_id,
                tenant.created_at,
            ),
        )

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        row = self._one("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        return Tenant(**dict(row)) if row else None

    def update_tenant(self, tenant_id: str, **fields: Any) -> None:
        allowed = {"name", "plan", "status", "billing_customer_id"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"can only update {sorted(allowed)}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE tenants SET {cols} WHERE id = ?", (*fields.values(), tenant_id))  # noqa: S608

    def find_tenant_by_customer(self, customer_id: str) -> Tenant | None:
        row = self._one("SELECT * FROM tenants WHERE billing_customer_id = ?", (customer_id,))
        return Tenant(**dict(row)) if row else None

    def list_tenants(self) -> list[Tenant]:
        return [Tenant(**dict(r)) for r in self._all("SELECT * FROM tenants ORDER BY created_at")]

    # users ---------------------------------------------------------------
    def add_user(self, user: User) -> None:
        self._exec(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?,?,?,?,?)",
            (user.id, user.email, user.name, user.password_hash, user.created_at),
        )

    def get_user(self, user_id: str) -> User | None:
        row = self._one("SELECT * FROM users WHERE id = ?", (user_id,))
        return User(**dict(row)) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        row = self._one("SELECT * FROM users WHERE email = ?", (email,))
        return User(**dict(row)) if row else None

    # memberships ---------------------------------------------------------
    def set_membership(self, tenant_id: str, user_id: str, role: str) -> None:
        self._exec(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (?,?,?) "
            "ON CONFLICT(tenant_id, user_id) DO UPDATE SET role = excluded.role",
            (tenant_id, user_id, role),
        )

    def remove_membership(self, tenant_id: str, user_id: str) -> None:
        self._exec("DELETE FROM memberships WHERE tenant_id = ? AND user_id = ?", (tenant_id, user_id))

    def get_role(self, tenant_id: str, user_id: str) -> str | None:
        row = self._one("SELECT role FROM memberships WHERE tenant_id = ? AND user_id = ?", (tenant_id, user_id))
        return str(row["role"]) if row else None

    def members(self, tenant_id: str) -> list[tuple[User, str]]:
        rows = self._all(
            "SELECT u.*, m.role AS role FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.tenant_id = ? ORDER BY u.created_at",
            (tenant_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            role = d.pop("role")
            out.append((User(**d), role))
        return out

    def tenants_for_user(self, user_id: str) -> list[tuple[Tenant, str]]:
        rows = self._all(
            "SELECT t.*, m.role AS role FROM memberships m JOIN tenants t ON t.id = m.tenant_id "
            "WHERE m.user_id = ? ORDER BY t.created_at",
            (user_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            role = d.pop("role")
            out.append((Tenant(**d), role))
        return out

    # keys ----------------------------------------------------------------
    def add_key(self, key: ApiKey) -> None:
        self._exec(
            "INSERT INTO api_keys (id, tenant_id, user_id, prefix, digest, name, created_at) VALUES (?,?,?,?,?,?,?)",
            (key.id, key.tenant_id, key.user_id, key.prefix, key.digest, key.name, key.created_at),
        )

    def get_key_by_prefix(self, prefix: str) -> ApiKey | None:
        row = self._one("SELECT * FROM api_keys WHERE prefix = ?", (prefix,))
        return ApiKey(**dict(row)) if row else None

    def list_keys(self, tenant_id: str) -> list[ApiKey]:
        rows = self._all("SELECT * FROM api_keys WHERE tenant_id = ? ORDER BY created_at", (tenant_id,))
        return [ApiKey(**dict(r)) for r in rows]

    def revoke_key(self, tenant_id: str, key_id: str) -> bool:
        cur = self._exec(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND tenant_id = ? AND revoked_at IS NULL",
            (time.time(), key_id, tenant_id),
        )
        return cur.rowcount > 0

    def touch_key(self, key_id: str) -> None:
        self._exec("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (time.time(), key_id))

    # usage ---------------------------------------------------------------
    def add_usage(self, tenant_id: str, period: str, metric: str, quantity: float) -> None:
        self._exec(
            "INSERT INTO usage (tenant_id, period, metric, quantity) VALUES (?,?,?,?) "
            "ON CONFLICT(tenant_id, period, metric) DO UPDATE SET quantity = quantity + excluded.quantity",
            (tenant_id, period, metric, quantity),
        )

    def get_usage(self, tenant_id: str, period: str) -> dict[str, float]:
        rows = self._all("SELECT metric, quantity FROM usage WHERE tenant_id = ? AND period = ?", (tenant_id, period))
        return {str(r["metric"]): float(r["quantity"]) for r in rows}

    def mark_event(self, event_id: str) -> bool:
        """Record a billing event id; False if it was already processed (webhook retries)."""
        try:
            self._exec("INSERT INTO billing_events (id, received_at) VALUES (?, ?)", (event_id, time.time()))
        except sqlite3.IntegrityError:
            return False
        return True

    # failed sign-ins -----------------------------------------------------
    def add_login_failure(self, key: str, at: float, prune_before: float) -> None:
        with self._lock:
            self._db.execute("DELETE FROM login_failures WHERE at < ?", (prune_before,))
            self._db.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", (key, at))

    def count_login_failures(self, key: str, since: float) -> tuple[int, float | None]:
        row = self._one(
            "SELECT COUNT(*) AS n, MIN(at) AS oldest FROM login_failures WHERE key = ? AND at >= ?", (key, since)
        )
        if row is None or not row["n"]:
            return 0, None
        return int(row["n"]), float(row["oldest"])

    def clear_login_failures(self, key: str) -> None:
        self._exec("DELETE FROM login_failures WHERE key = ?", (key,))


StoreFactory = Callable[[str], Store]
_STORES: dict[str, StoreFactory] = {"sqlite": lambda rest: SQLiteStore(rest or ":memory:")}


def register_store(scheme: str, factory: StoreFactory) -> None:
    """Make ``<scheme>://...`` database URLs build a store with ``factory(rest_of_url)``."""
    _STORES[scheme] = factory


def open_store(url: str) -> Store:
    """Open the store a database URL names (``sqlite:///platform.db``, ``sqlite:///:memory:``)."""
    for name, factory in discover(STORE_GROUP).items():
        _STORES.setdefault(name, factory)
    scheme, sep, rest = url.partition("://")
    if not sep:
        raise ValueError(f"database must be a URL like sqlite:///platform.db, got {url!r}")
    if scheme not in _STORES:
        raise ValueError(f"no platform store for '{scheme}://'. Available: {', '.join(sorted(_STORES))}")
    if scheme == "sqlite":
        rest = rest[1:] if rest.startswith("/") else rest  # sqlite:///rel.db -> rel.db, sqlite:////abs -> /abs
    return _STORES[scheme](rest)
