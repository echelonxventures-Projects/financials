"""Utilities: validation, rate-limiting, error handling."""
from __future__ import annotations

from financial_model.utils.error_handler import (
    CalculationError,
    DataFetchError,
    ErrorCollector,
    ExternalServiceError,
    retry,
    with_error_handling,
)
from financial_model.utils.error_handler import (
    ValidationError as ErrorValidation,
)
from financial_model.utils.rate_limiter import (
    CircuitBreakerOpen,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    check_rate_limit,
    get_rate_limiter,
    record_fetch_failure,
    record_fetch_success,
)
from financial_model.utils.validation import (
    DivisionSafety,
    FinancialRangeValidator,
    InputSanitizer,
    TickerValidator,
    ValidationError,
    ValidationResult,
    ValidationSeverity,
    validate_financial_inputs,
)

__all__ = [
    # Error handling
    "ExternalServiceError",
    "DataFetchError",
    "ErrorValidation",
    "CalculationError",
    "retry",
    "with_error_handling",
    "ErrorCollector",
    # Validation
    "ValidationError",
    "ValidationSeverity",
    "ValidationResult",
    "TickerValidator",
    "FinancialRangeValidator",
    "DivisionSafety",
    "InputSanitizer",
    "validate_financial_inputs",
    # Rate limiting
    "RateLimitConfig",
    "CircuitBreakerOpen",
    "RateLimitExceeded",
    "RateLimiter",
    "get_rate_limiter",
    "check_rate_limit",
    "record_fetch_success",
    "record_fetch_failure",
]
