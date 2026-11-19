"""Rate-limited data fetching with circuit breaker pattern."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    max_requests_per_minute: int = 30
    max_requests_per_hour: int = 500
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0

    def __post_init__(self):
        if self.max_requests_per_minute <= 0:
            raise ValueError("max_requests_per_minute must be positive")
        if self.max_requests_per_hour <= 0:
            raise ValueError("max_requests_per_hour must be positive")


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open (too many failures)."""

    pass


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""

    pass


class RateLimiter:
    """Thread-safe rate limiter with sliding window and circuit breaker.

    Features:
    - Per-endpoint request tracking
    - Sliding window rate limiting (per-minute and per-hour)
    - Circuit breaker pattern to prevent cascading failures
    - Automatic recovery after timeout
    """

    def __init__(self, config: RateLimitConfig | None = None):
        self._config = config or RateLimitConfig()
        self._request_times: dict[str, list[float]] = defaultdict(list)
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._circuit_open_since: dict[str, float] = {}
        self._global_request_times: list[float] = []
        self._lock = threading.RLock()

    @property
    def config(self) -> RateLimitConfig:
        return self._config

    def _cleanup_old_requests(self, endpoint: str, max_age: float) -> None:
        """Remove requests older than max_age from the window."""
        now = time.time()
        if endpoint in self._request_times:
            self._request_times[endpoint] = [t for t in self._request_times[endpoint] if now - t < max_age]

    def _cleanup_global(self, max_age: float) -> None:
        """Remove old global requests."""
        now = time.time()
        self._global_request_times = [t for t in self._global_request_times if now - t < max_age]

    def is_circuit_open(self, endpoint: str) -> bool:
        """Check if circuit breaker is open for endpoint."""
        if endpoint not in self._circuit_open_since:
            return False
        elapsed = time.time() - self._circuit_open_since[endpoint]
        if elapsed >= self._config.circuit_breaker_timeout:
            # Auto-recover: allow one trial request
            del self._circuit_open_since[endpoint]
            self._failure_counts[endpoint] = 0
            return False
        return True

    def open_circuit(self, endpoint: str) -> None:
        """Open the circuit breaker for an endpoint."""
        if endpoint not in self._circuit_open_since:
            self._circuit_open_since[endpoint] = time.time()

    def record_request(self, endpoint: str = "default") -> bool:
        """Record a request. Returns True if allowed; raises when limited or the circuit is open."""
        with self._lock:
            return self._record_request(endpoint)

    def _record_request(self, endpoint: str) -> bool:
        now = time.time()

        # Check circuit breaker
        if self.is_circuit_open(endpoint):
            raise CircuitBreakerOpen(
                f"Circuit breaker open for '{endpoint}'; retry after {self._config.circuit_breaker_timeout}s"
            )

        # Per-endpoint per-minute check
        self._cleanup_old_requests(endpoint, 60.0)
        if len(self._request_times[endpoint]) >= self._config.max_requests_per_minute:
            raise RateLimitExceeded(
                f"Per-minute rate limit exceeded for '{endpoint}': {self._config.max_requests_per_minute}/min"
            )

        # Global per-hour check
        self._cleanup_global(3600.0)
        if len(self._global_request_times) >= self._config.max_requests_per_hour:
            raise RateLimitExceeded(f"Global per-hour rate limit exceeded: {self._config.max_requests_per_hour}/hr")

        # Record the request
        self._request_times[endpoint].append(now)
        self._global_request_times.append(now)
        return True

    def record_failure(self, endpoint: str) -> None:
        """Record a failed request (may open circuit breaker)."""
        with self._lock:
            self._failure_counts[endpoint] += 1
            if self._failure_counts[endpoint] >= self._config.circuit_breaker_threshold:
                self.open_circuit(endpoint)

    def record_success(self, endpoint: str) -> None:
        """Record a successful request (resets failure count)."""
        with self._lock:
            self._failure_counts[endpoint] = 0

    def get_stats(self) -> dict[str, Any]:
        """Get current rate limiter statistics."""
        now = time.time()
        self._cleanup_global(3600.0)
        return {
            "active_endpoints": len(self._request_times),
            "global_requests_last_hour": len(self._global_request_times),
            "open_circuits": {ep: round(now - ts, 1) for ep, ts in self._circuit_open_since.items()},
            "failure_counts": dict(self._failure_counts),
        }


# Process-wide limiter, built lazily from the ``data.rate_limit`` config section
_default_limiter: RateLimiter | None = None
_default_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance (configured from ``get_config()['data']['rate_limit']``)."""
    global _default_limiter
    with _default_lock:
        if _default_limiter is None:
            from financial_model.config_loader import get_config

            settings = get_config().get("data", {}).get("rate_limit", {}) or {}
            fields = RateLimitConfig.__dataclass_fields__
            _default_limiter = RateLimiter(RateLimitConfig(**{k: v for k, v in settings.items() if k in fields}))
        return _default_limiter


def reset_rate_limiter(limiter: RateLimiter | None = None) -> None:
    """Replace (or clear, to rebuild from config) the global limiter. Intended for tests."""
    global _default_limiter
    with _default_lock:
        _default_limiter = limiter


def check_rate_limit(endpoint: str = "yahoo_finance") -> bool:
    """Check and record a rate-limited request.

    Raises:
        RateLimitExceeded: If rate limit exceeded.
        CircuitBreakerOpen: If circuit breaker is open.
    """
    return get_rate_limiter().record_request(endpoint)


def record_fetch_success(endpoint: str = "yahoo_finance") -> None:
    """Record a successful data fetch."""
    get_rate_limiter().record_success(endpoint)


def record_fetch_failure(endpoint: str = "yahoo_finance") -> None:
    """Record a failed data fetch."""
    get_rate_limiter().record_failure(endpoint)
