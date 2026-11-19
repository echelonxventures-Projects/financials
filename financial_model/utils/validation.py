"""Input validation utilities with comprehensive financial data guards."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


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

    _PATTERNS = {
        # Major US exchanges
        "NASDAQ": re.compile(r"^[A-Z]{1,6}$"),
        "NYSE": re.compile(r"^[A-Z]{1,4}$"),
        # India (NSE/BSE)
        "NSE": re.compile(r"^[A-Z0-9]{1,8}$"),
        "BSE": re.compile(r"^\d{6}$"),  # 6-digit scrip codes, e.g. 500325
        # International
        "LSE": re.compile(r"^[A-Z]{1,4}$"),
        "TSE": re.compile(r"^\d[0-9A-Z]{3}$"),  # 4-char codes: 7203, and alphanumeric since 2024 (130A)
        # Generic fallback: 1-10 alphanumerics, an optional share-class / exchange
        # suffix (BRK-B, RELIANCE.NS, 7203.T) or a colon exchange prefix (AAPL:NAS),
        # and Yahoo's ^ prefix for indices (^GSPC).
        "GENERIC": re.compile(r"^\^?[A-Z0-9]{1,10}([.\-][A-Z0-9]{1,5})?(:[A-Z]{2,5})?$"),
    }

    @classmethod
    def validate(cls, ticker: str, exchange: str = "GENERIC") -> ValidationResult:
        if not ticker or not isinstance(ticker, str):
            return ValidationResult(
                is_valid=False,
                errors=[ValidationError("ticker", "Ticker must be a non-empty string", ticker)],
                warnings=[],
            )

        ticker = ticker.strip().upper()
        pattern = cls._PATTERNS.get(exchange, cls._PATTERNS["GENERIC"])

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

    # Bounds for common financial metrics (min, max)
    _BOUNDS = {
        "revenue_growth_rate": (-0.5, 5.0),
        "ebitda_margin": (0.0, 1.0),
        "tax_rate": (0.0, 0.6),
        "capex_pct_revenue": (0.0, 1.0),
        "drc_pct_revenue": (0.0, 1.0),
        "wacc": (0.01, 0.40),
        "terminal_growth_rate": (-0.05, 0.05),
        "discount_rate": (0.01, 0.50),
        "debt_to_equity": (0.0, 10.0),
        "leveraged_buyout_multiple": (1.0, 20.0),
        "exit_multiple": (1.0, 25.0),
        "senior_debt_cost": (0.01, 0.15),
        "subordinated_debt_cost": (0.05, 0.20),
        "equity_multiple_target": (1.0, 5.0),
        "holding_period": (1, 10),
        "revenue": (0, float("inf")),
        "ebitda": (0, float("inf")),
        "multiple": (0.1, 50.0),
        "shares_outstanding": (1, 10_000_000_000),
        "market_cap": (0, float("inf")),
        "beta": (-2.0, 5.0),
        "risk_free_rate": (0.0, 0.15),
        "equity_risk_premium": (0.0, 0.15),
        "probability": (0.0, 1.0),
        "weight": (0.0, 1.0),
    }

    @classmethod
    def validate_value(cls, name: str, value: float) -> ValidationResult:
        if name not in cls._BOUNDS:
            return ValidationResult(is_valid=True, errors=[], warnings=[f"No bounds defined for '{name}'"])

        min_val, max_val = cls._BOUNDS[name]
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
