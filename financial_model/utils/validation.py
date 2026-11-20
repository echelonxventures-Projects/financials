"""Input validation utilities with comprehensive financial data guards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any


def _validation_settings() -> dict[str, Any]:
    from financial_model.config_loader import get_config

    return dict(get_config().get("validation") or {})


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


class ValidationError(ValueError):
    """Raised when input validation fails."""

    def __init__(self, field: str, message: str, value: Any = None):
        self.field = field
        self.message = message
        self.value = value
        suffix = f" (got {value!r})" if value is not None else ""
        super().__init__(f"Validation error in '{field}': {message}{suffix}")


class ValidationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationResult:
    """Result of a validation run."""

    is_valid: bool
    errors: list[ValidationError]
    warnings: list[str]

    def __bool__(self) -> bool:
        return self.is_valid

    @property
    def all_messages(self) -> list[str]:
        return [str(e) for e in self.errors] + self.warnings

    def raise_if_invalid(self) -> None:
        """Raise the first error, if any."""
        if self.errors:
            raise self.errors[0]


class TickerValidator:
    """Validate stock ticker symbols against exchange patterns."""

    @staticmethod
    def patterns() -> dict[str, re.Pattern[str]]:
        """Exchange -> pattern, from the ``validation.tickers`` settings."""
        return {str(k): _compiled(str(v)) for k, v in (_validation_settings().get("tickers") or {}).items()}

    @classmethod
    def validate(cls, ticker: str, exchange: str = "GENERIC") -> ValidationResult:
        if not ticker or not isinstance(ticker, str):
            return ValidationResult(
                is_valid=False,
                errors=[ValidationError("ticker", "Ticker must be a non-empty string", ticker)],
                warnings=[],
            )

        ticker = ticker.strip().upper()
        patterns = cls.patterns()
        pattern = patterns.get(exchange) or patterns.get("GENERIC")
        if pattern is None:
            return ValidationResult(is_valid=True, errors=[], warnings=[f"No ticker pattern for '{exchange}'"])

        if not pattern.match(ticker):
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        "ticker",
                        f"Ticker '{ticker}' does not match {exchange} pattern",
                        ticker,
                    )
                ],
                warnings=[],
            )

        return ValidationResult(is_valid=True, errors=[], warnings=[])


class FinancialRangeValidator:
    """Validate financial parameters are within realistic ranges."""

    @staticmethod
    def bounds() -> dict[str, tuple[float, float]]:
        """Name -> (min, max), from the ``validation.ranges`` settings (null = unbounded)."""
        out: dict[str, tuple[float, float]] = {}
        for name, pair in (_validation_settings().get("ranges") or {}).items():
            lo, hi = (list(pair) + [None, None])[:2]
            out[str(name)] = (float("-inf") if lo is None else lo, float("inf") if hi is None else hi)
        return out

    @classmethod
    def validate_value(cls, name: str, value: float) -> ValidationResult:
        bounds = cls.bounds()
        if name not in bounds:
            return ValidationResult(is_valid=True, errors=[], warnings=[f"No bounds defined for '{name}'"])

        min_val, max_val = bounds[name]
        if value < min_val or value > max_val:
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        name,
                        f"{name}={value} is outside valid range [{min_val}, {max_val}]",
                        value,
                    )
                ],
                warnings=[],
            )

        return ValidationResult(is_valid=True, errors=[], warnings=[])

    @classmethod
    def validate_params(cls, params: dict[str, float]) -> ValidationResult:
        all_errors: list[ValidationError] = []
        all_warnings: list[str] = []

        for key, value in params.items():
            result = cls.validate_value(key, value)
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)

        return ValidationResult(
            is_valid=len(all_errors) == 0,
            errors=all_errors,
            warnings=all_warnings,
        )


class DivisionSafety:
    """Guard against division by zero in financial calculations."""

    @staticmethod
    def safe_divide(numerator: float, denominator: float, fallback: float = 0.0) -> float:
        """Divide with zero-safe fallback."""
        if denominator == 0:
            return fallback
        return numerator / denominator

    @staticmethod
    def safe_percentage(part: float, whole: float) -> float:
        """Calculate percentage with zero-safe fallback."""
        if whole == 0:
            return 0.0
        return part / whole


class InputSanitizer:
    """Sanitize raw input from external sources (API, file, CLI)."""

    @staticmethod
    def sanitize_string(value: Any) -> str:
        """Strip, normalize whitespace, remove null bytes."""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.strip().replace("\x00", "").replace("\t", " ")

    @staticmethod
    def sanitize_float(value: Any, default: float | None = 0.0) -> float | None:
        """Convert to float safely."""
        if value is None:
            return default
        try:
            result = float(value)
            if not (result == result):  # NaN check
                return default
            if result == float("inf") or result == float("-inf"):
                return default
            return result
        except (ValueError, TypeError):
            return default

    @staticmethod
    def sanitize_int(value: Any, default: int = 0) -> int:
        """Convert to int safely."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default


def validate_financial_inputs(
    params: dict[str, Any], required_fields: list[str], range_checks: bool = True
) -> ValidationResult:
    """Comprehensive validation for financial model inputs.

    Checks:
    1. All required fields present
    2. All values are numeric (float-convertible)
    3. All values within realistic financial ranges (if range_checks=True)
    4. No NaN or Infinity values
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []

    # Check required fields
    for field in required_fields:
        if field not in params or params[field] is None:
            errors.append(ValidationError(field, f"Required field '{field}' is missing"))

    # Check numeric values
    for field, value in params.items():
        if field not in required_fields:
            continue
        if value is None:
            continue
        sanitized = InputSanitizer.sanitize_float(value, None)
        if sanitized is None:
            errors.append(ValidationError(field, f"'{field}' must be numeric", value))

    # Range checks
    if range_checks:
        numeric_params = {}
        for field, value in params.items():
            if field in required_fields and value is not None:
                try:
                    numeric_params[field] = float(value)
                except (ValueError, TypeError):
                    pass  # Already flagged above
        range_result = FinancialRangeValidator.validate_params(numeric_params)
        errors.extend(range_result.errors)
        warnings.extend(range_result.warnings)

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
