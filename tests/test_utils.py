"""Tests for financial_model.utils: validation, rate limiting, error handling."""

import threading

import pytest

from financial_model.utils import error_handler, rate_limiter
from financial_model.utils.error_handler import (
    CalculationError,
    DataFetchError,
    ErrorCollector,
    ExternalServiceError,
    retry,
    with_error_handling,
)
from financial_model.utils.error_handler import ValidationError as ServiceValidationError
from financial_model.utils.rate_limiter import (
    CircuitBreakerOpen,
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    check_rate_limit,
    get_rate_limiter,
    record_fetch_failure,
    record_fetch_success,
    reset_rate_limiter,
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

# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestValidationError:
    def test_fields(self):
        e = ValidationError("revenue", "must be positive", -1)
        assert (e.field, e.message, e.value) == ("revenue", "must be positive", -1)

    def test_str_includes_field_message_and_value(self):
        assert str(ValidationError("revenue", "must be positive", -1)) == (
            "Validation error in 'revenue': must be positive (got -1)"
        )
        assert "got" not in str(ValidationError("revenue", "must be positive"))

    def test_is_value_error(self):
        with pytest.raises(ValueError):
            raise ValidationError("x", "bad")


class TestValidationResult:
    def test_truthiness_follows_is_valid(self):
        assert ValidationResult(True, [], [])
        assert not ValidationResult(False, [ValidationError("x", "bad")], [])

    def test_raise_if_invalid(self):
        ValidationResult(True, [], []).raise_if_invalid()
        with pytest.raises(ValidationError, match="bad"):
            ValidationResult(False, [ValidationError("x", "bad")], []).raise_if_invalid()

    def test_all_messages(self):
        r = ValidationResult(False, [ValidationError("x", "bad")], ["careful"])
        assert r.all_messages == ["Validation error in 'x': bad", "careful"]

    def test_severity_values(self):
        assert [s.value for s in ValidationSeverity] == ["error", "warning", "info"]


class TestTickerValidator:
    @pytest.mark.parametrize(
        "ticker",
        ["AAPL", "aapl", " MSFT ", "BRK-B", "BRK.B", "RELIANCE.NS", "7203.T", "^GSPC", "AAPL:NASDQ", "A"],
    )
    def test_generic_accepts_real_world_symbols(self, ticker):
        assert TickerValidator.validate(ticker)

    @pytest.mark.parametrize("ticker", ["", "AAPL$", "A" * 11, "AA PL", "../etc", "AAPL;DROP", None, 123])
    def test_generic_rejects_malformed(self, ticker):
        result = TickerValidator.validate(ticker)
        assert not result
        assert result.errors[0].field == "ticker"

    @pytest.mark.parametrize(
        "ticker,exchange,ok",
        [
            ("GOOGL", "NASDAQ", True),
            ("GOOGLELLC", "NASDAQ", False),
            ("IBM", "NYSE", True),
            ("EXTRA", "NYSE", False),
            ("TATA100", "NSE", True),
            ("TOOLONG99", "NSE", False),
            ("500325", "BSE", True),  # BSE scrip codes are 6 digits
            ("53221", "BSE", False),
            ("ABCDEF", "BSE", False),
            ("7203", "TSE", True),
            ("130A", "TSE", True),  # alphanumeric codes issued since 2024
            ("720", "TSE", False),
            ("72030", "TSE", False),
        ],
    )
    def test_exchange_patterns(self, ticker, exchange, ok):
        assert bool(TickerValidator.validate(ticker, exchange)) is ok

    def test_unknown_exchange_falls_back_to_generic(self):
        assert TickerValidator.validate("AAPL", "UNKNOWN")


class TestFinancialRangeValidator:
    def test_in_range(self):
        assert FinancialRangeValidator.validate_value("tax_rate", 0.21)

    def test_out_of_range(self):
        r = FinancialRangeValidator.validate_value("tax_rate", 0.9)
        assert not r and "outside valid range" in str(r.errors[0])

    def test_unknown_name_warns_but_passes(self):
        r = FinancialRangeValidator.validate_value("unknown_metric", 42)
        assert r and r.warnings

    def test_validate_params_collects_all_errors(self):
        r = FinancialRangeValidator.validate_params({"tax_rate": 0.9, "wacc": 0.9, "beta": 1.1})
        assert not r
        assert {e.field for e in r.errors} == {"tax_rate", "wacc"}


class TestDivisionSafety:
    def test_safe_divide(self):
        assert DivisionSafety.safe_divide(10, 4) == 2.5
        assert DivisionSafety.safe_divide(10, 0) == 0.0
        assert DivisionSafety.safe_divide(10, 0, fallback=-1.0) == -1.0

    def test_safe_percentage(self):
        assert DivisionSafety.safe_percentage(25, 100) == 0.25
        assert DivisionSafety.safe_percentage(25, 0) == 0.0


class TestInputSanitizer:
    @pytest.mark.parametrize(
        "raw,expected",
        [("  hello  ", "hello"), ("a\tb", "a b"), ("x\x00y", "xy"), (None, ""), (42, "42"), (3.5, "3.5"), ("世界", "世界")],
    )
    def test_sanitize_string(self, raw, expected):
        assert InputSanitizer.sanitize_string(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [(3.14, 3.14), ("2.5", 2.5), (None, 0.0), ("abc", 0.0), (float("nan"), 0.0), (float("inf"), 0.0)],
    )
    def test_sanitize_float(self, raw, expected):
        assert InputSanitizer.sanitize_float(raw) == expected

    def test_sanitize_float_custom_default(self):
        assert InputSanitizer.sanitize_float("x", default=None) is None

    @pytest.mark.parametrize("raw,expected", [(42, 42), ("42", 42), (42.9, 42), (None, 0), ("abc", 0)])
    def test_sanitize_int(self, raw, expected):
        assert InputSanitizer.sanitize_int(raw) == expected
        assert InputSanitizer.sanitize_int("abc", default=7) == 7


class TestValidateFinancialInputs:
    def test_valid(self):
        assert validate_financial_inputs({"tax_rate": 0.21, "wacc": 0.09}, ["tax_rate", "wacc"])

    def test_missing_required(self):
        r = validate_financial_inputs({"tax_rate": 0.21}, ["tax_rate", "wacc"])
        assert not r and r.errors[0].field == "wacc"

    def test_non_numeric(self):
        r = validate_financial_inputs({"wacc": "high"}, ["wacc"])
        assert any("numeric" in str(e) for e in r.errors)

    def test_range_checks_can_be_disabled(self):
        assert validate_financial_inputs({"tax_rate": 0.9}, ["tax_rate"], range_checks=False)
        assert not validate_financial_inputs({"tax_rate": 0.9}, ["tax_rate"])


# --------------------------------------------------------------------------- #
# Rate limiter
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(rate_limiter.time, "time", c)
    return c


class TestRateLimitConfig:
    def test_rejects_non_positive_limits(self):
        with pytest.raises(ValueError):
            RateLimitConfig(max_requests_per_minute=0)
        with pytest.raises(ValueError):
            RateLimitConfig(max_requests_per_hour=-1)


class TestRateLimiter:
    def test_per_minute_limit_and_window_slides(self, clock):
        rl = RateLimiter(RateLimitConfig(max_requests_per_minute=2, max_requests_per_hour=100))
        assert rl.record_request("e") and rl.record_request("e")
        with pytest.raises(RateLimitExceeded, match="Per-minute"):
            rl.record_request("e")
        rl.record_request("other")  # limits are per endpoint
        clock.now += 61
        assert rl.record_request("e")

    def test_global_hourly_limit(self, clock):
        rl = RateLimiter(RateLimitConfig(max_requests_per_minute=100, max_requests_per_hour=3))
        for ep in ("a", "b", "c"):
            rl.record_request(ep)
        with pytest.raises(RateLimitExceeded, match="per-hour"):
            rl.record_request("d")
        clock.now += 3601
        assert rl.record_request("d")

    def test_circuit_breaker_opens_and_recovers(self, clock):
        rl = RateLimiter(RateLimitConfig(circuit_breaker_threshold=2, circuit_breaker_timeout=30))
        rl.record_failure("e")
        assert not rl.is_circuit_open("e")
        rl.record_failure("e")
        with pytest.raises(CircuitBreakerOpen):
            rl.record_request("e")
        clock.now += 31
        assert rl.record_request("e")
        assert rl.get_stats()["failure_counts"]["e"] == 0

    def test_success_resets_failures(self, clock):
        rl = RateLimiter(RateLimitConfig(circuit_breaker_threshold=2))
        rl.record_failure("e")
        rl.record_success("e")
        rl.record_failure("e")
        assert not rl.is_circuit_open("e")

    def test_stats(self, clock):
        rl = RateLimiter()
        rl.record_request("e")
        stats = rl.get_stats()
        assert stats["global_requests_last_hour"] == 1
        assert stats["active_endpoints"] == 1

    def test_concurrent_requests_never_exceed_limit(self):
        rl = RateLimiter(RateLimitConfig(max_requests_per_minute=50, max_requests_per_hour=1000))
        allowed = []

        def worker():
            for _ in range(20):
                try:
                    rl.record_request("e")
                    allowed.append(1)
                except RateLimitExceeded:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(allowed) == 50


class TestGlobalLimiter:
    @pytest.fixture(autouse=True)
    def fresh(self):
        reset_rate_limiter()
        yield
        reset_rate_limiter()

    def test_built_from_settings(self, monkeypatch):
        from financial_model import config_loader

        monkeypatch.setenv("FIN_DATA_RATE_LIMIT_MAX_REQUESTS_PER_MINUTE", "1")
        config_loader._reset_cache()
        try:
            assert get_rate_limiter().config.max_requests_per_minute == 1
            check_rate_limit("x")
            with pytest.raises(RateLimitExceeded):
                check_rate_limit("x")
        finally:
            monkeypatch.delenv("FIN_DATA_RATE_LIMIT_MAX_REQUESTS_PER_MINUTE")
            config_loader._reset_cache()

    def test_module_helpers_use_singleton(self):
        limiter = RateLimiter(RateLimitConfig(circuit_breaker_threshold=1))
        reset_rate_limiter(limiter)
        assert get_rate_limiter() is limiter
        record_fetch_failure("y")
        assert limiter.is_circuit_open("y")
        record_fetch_success("y")
        assert limiter.get_stats()["failure_counts"]["y"] == 0


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


class TestErrors:
    def test_external_service_error(self):
        e = ExternalServiceError("timeout", service="yfinance")
        assert str(e) == "yfinance: timeout" and e.retryable

    def test_subclasses(self):
        assert issubclass(DataFetchError, ExternalServiceError)
        v = ServiceValidationError("bad", field="ticker")
        assert v.field == "ticker" and not v.retryable and "ticker: bad" in str(v)
        c = CalculationError("diverged", model_type="lbo")
        assert c.model_type == "lbo" and not c.retryable


class TestRetry:
    @pytest.fixture(autouse=True)
    def no_sleep(self, monkeypatch):
        self.sleeps = []
        monkeypatch.setattr(error_handler.time, "sleep", self.sleeps.append)

    def test_retries_then_succeeds_with_backoff(self):
        calls = {"n": 0}

        @retry(max_attempts=4, base_delay=1.0, backoff_factor=2.0, max_delay=3.0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 4:
                raise DataFetchError("try again")
            return "ok"

        assert flaky() == "ok"
        assert self.sleeps == [1.0, 2.0, 3.0]  # capped at max_delay

    def test_gives_up_after_max_attempts(self):
        @retry(max_attempts=2, base_delay=0)
        def always():
            raise DataFetchError("down")

        with pytest.raises(DataFetchError):
            always()
        assert len(self.sleeps) == 1

    def test_does_not_retry_other_exceptions(self):
        @retry(max_attempts=3)
        def boom():
            raise KeyError("x")

        with pytest.raises(KeyError):
            boom()
        assert self.sleeps == []


class TestWithErrorHandling:
    def test_returns_default_on_error(self):
        @with_error_handling("svc", default_return={})
        def bad():
            raise RuntimeError("x")

        assert bad() == {}

    def test_passes_through_result(self):
        @with_error_handling()
        def good():
            return 1

        assert good() == 1

    def test_only_catches_listed_exceptions(self):
        @with_error_handling(exceptions=(KeyError,))
        def bad():
            raise ValueError("x")

        with pytest.raises(ValueError):
            bad()


class TestErrorCollector:
    def test_collects_and_summarises(self):
        ec = ErrorCollector()
        assert not ec.has_errors() and ec.get_summary() == "No issues"
        ec.add_warning("w")
        ec.add_error("e")
        assert ec.has_errors()
        assert "Errors (1)" in ec.get_summary() and "Warnings (1)" in ec.get_summary()
        ec.clear()
        assert not ec.has_errors()
