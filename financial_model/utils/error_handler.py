"""Centralized error handling and retry logic for data operations."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


class ExternalServiceError(Exception):
    """Base error for external service failures."""

    def __init__(self, message: str, service: str = "", retryable: bool = True):
        self.service = service
        self.retryable = retryable
        super().__init__(f"{service}: {message}" if service else message)


class DataFetchError(ExternalServiceError):
    """Error fetching data from external source."""

    pass


class ValidationError(ExternalServiceError):
    """Input validation error."""

    def __init__(self, message: str, field: str = ""):
        self.field = field
        super().__init__(
            f"{field}: {message}" if field else message,
            service="validation",
            retryable=False,
        )


class CalculationError(ExternalServiceError):
    """Error during financial calculation."""

    def __init__(self, message: str, model_type: str = ""):
        self.model_type = model_type
        super().__init__(
            f"{model_type}: {message}" if model_type else message,
            service="calculation",
            retryable=False,
        )


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (ExternalServiceError,),
):
    """Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        backoff_factor: Multiplier for each retry delay.
        exceptions: Tuple of exception types to catch and retry on.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            delay = base_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}, retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

            # Should not reach here, but just in case
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator


def with_error_handling(
    service_name: str = "",
    default_return: Any = None,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Decorator that catches exceptions and returns a safe fallback.

    Args:
        service_name: Name of the service for logging.
        default_return: Value to return on error.
        exceptions: Tuple of exception types to catch.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                label = f"{service_name}:{func.__name__}" if service_name else func.__name__
                logger.error(f"Error in {label}: {e}", exc_info=True)
                return default_return

        return wrapper

    return decorator


class ErrorCollector:
    """Collect and manage errors across multi-step operations."""

    def __init__(self):
        self._errors: list[str] = []
        self._warnings: list[str] = []

    def add_error(self, message: str) -> None:
        self._errors.append(message)
        logger.error(message)

    def add_warning(self, message: str) -> None:
        self._warnings.append(message)
        logger.warning(message)

    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def get_summary(self) -> str:
        parts = []
        if self._errors:
            parts.append(f"Errors ({len(self._errors)}): {self._errors}")
        if self._warnings:
            parts.append(f"Warnings ({len(self._warnings)}): {self._warnings}")
        return "; ".join(parts) if parts else "No issues"

    def clear(self) -> None:
        self._errors.clear()
        self._warnings.clear()
