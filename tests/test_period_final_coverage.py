"""
Tests for period module to achieve 100% coverage - targeting uncovered lines.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_model.core.period import Period, PeriodIndex, PeriodType


class TestPeriodUncoveredLines:
    """Test Period edge cases for uncovered lines."""

    def test_period_annual_post_init(self):
        """Test Period post_init for annual (lines 76, 80, 88, 92)."""
        # Line 32-34: annual sets quarter/month to None
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p.quarter is None
        assert p.month is None

    def test_period_quarterly_validation(self):
        """Test Period validation for quarterly."""
        # Line 28-29: quarter required for quarterly
        with pytest.raises(ValueError, match="Quarter required"):
            Period(year=2024, period_type=PeriodType.QUARTERLY)

    def test_period_monthly_validation(self):
        """Test Period validation for monthly."""
        # Line 30-31: month required for monthly
        with pytest.raises(ValueError, match="Month required"):
            Period(year=2024, period_type=PeriodType.MONTHLY)

    def test_period_end_date_quarterly_q4(self):
        """Test end_date for Q4 (line 52)."""
        p = Period(year=2024, quarter=4, period_type=PeriodType.QUARTERLY)
        assert p.end_date == date(2024, 12, 31)

    def test_period_end_date_quarterly_q1_q3(self):
        """Test end_date for Q1-Q3 (lines 53-55)."""
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        assert p.end_date == date(2024, 3, 31)
        p = Period(year=2024, quarter=2, period_type=PeriodType.QUARTERLY)
        assert p.end_date == date(2024, 6, 30)
        p = Period(year=2024, quarter=3, period_type=PeriodType.QUARTERLY)
        assert p.end_date == date(2024, 9, 30)

    def test_period_end_date_monthly(self):
        """Test end_date for monthly (lines 57-59)."""
        p = Period(year=2024, month=1, period_type=PeriodType.MONTHLY)
        assert p.end_date == date(2024, 1, 31)
        p = Period(year=2024, month=2, period_type=PeriodType.MONTHLY)
        assert p.end_date == date(2024, 2, 29)  # Leap year
        p = Period(year=2023, month=2, period_type=PeriodType.MONTHLY)
        assert p.end_date == date(2023, 2, 28)  # Non-leap year

    def test_period_next_quarterly_wraps(self):
        """Test next() for quarterly wraps year (line 75-76)."""
        p = Period(year=2024, quarter=4, period_type=PeriodType.QUARTERLY)
        next_p = p.next()
        assert next_p.year == 2025
        assert next_p.quarter == 1

    def test_period_next_monthly_wraps(self):
        """Test next() for monthly wraps year (line 79-80)."""
        p = Period(year=2024, month=12, period_type=PeriodType.MONTHLY)
        next_p = p.next()
        assert next_p.year == 2025
        assert next_p.month == 1

    def test_period_prev_quarterly_wraps(self):
        """Test prev() for quarterly wraps year (line 87-88)."""
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        prev_p = p.prev()
        assert prev_p.year == 2023
        assert prev_p.quarter == 4

    def test_period_prev_monthly_wraps(self):
        """Test prev() for monthly wraps year (line 91-92)."""
        p = Period(year=2024, month=1, period_type=PeriodType.MONTHLY)
        prev_p = p.prev()
        assert prev_p.year == 2023
        assert prev_p.month == 12

    def test_period_range_inclusive(self):
        """Test Period.range inclusive (line 116)."""
        p1 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2026, period_type=PeriodType.ANNUAL)
        periods = Period.range(p1, p2, inclusive=True)
        assert len(periods) == 3
        assert periods[0].year == 2024
        assert periods[2].year == 2026

    def test_period_range_exclusive(self):
        """Test Period.range exclusive (line 116)."""
        p1 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2026, period_type=PeriodType.ANNUAL)
        periods = Period.range(p1, p2, inclusive=False)
        assert len(periods) == 2
        assert periods[0].year == 2024
        assert periods[1].year == 2025

    def test_period_from_date_annual(self):
        """Test Period.from_date for annual (line 100)."""
        p = Period.from_date(date(2024, 6, 15), PeriodType.ANNUAL)
        assert p.year == 2024
        assert p.period_type == PeriodType.ANNUAL

    def test_period_from_date_quarterly(self):
        """Test Period.from_date for quarterly (line 102-103)."""
        p = Period.from_date(date(2024, 6, 15), PeriodType.QUARTERLY)
        assert p.year == 2024
        assert p.quarter == 2

    def test_period_from_date_monthly(self):
        """Test Period.from_date for monthly (line 105)."""
        p = Period.from_date(date(2024, 6, 15), PeriodType.MONTHLY)
        assert p.year == 2024
        assert p.month == 6


class TestPeriodIndexUncoveredLines:
    """Test PeriodIndex edge cases for uncovered lines."""

    def test_period_index_post_init_sorts(self):
        """Test PeriodIndex __post_init__ sorts periods (line 128)."""
        p1 = Period(year=2026, period_type=PeriodType.ANNUAL)
        p2 = Period(year=2024, period_type=PeriodType.ANNUAL)
        p3 = Period(year=2025, period_type=PeriodType.ANNUAL)
        pi = PeriodIndex(periods=[p1, p2, p3])
        assert pi.periods[0].year == 2024
        assert pi.periods[1].year == 2025
        assert pi.periods[2].year == 2026

    def test_period_index_iter(self):
        """Test PeriodIndex __iter__ (line 137)."""
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        count = 0
        for p in periods:
            count += 1
        assert count == 3

    def test_period_index_contains(self):
        """Test PeriodIndex __contains__ (line 140)."""
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        p = Period(year=2024, period_type=PeriodType.ANNUAL)
        assert p in periods
        p2 = Period(year=2025, period_type=PeriodType.ANNUAL)
        assert p2 not in periods

    def test_period_index_add_duplicate(self):
        """Test PeriodIndex add with duplicate (line 144-146)."""
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        periods.add(Period(year=2024, period_type=PeriodType.ANNUAL))
        assert len(periods) == 1

    def test_period_index_add_unsorted(self):
        """Test PeriodIndex add keeps sorted (line 145-146)."""
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        periods.add(Period(year=2023, period_type=PeriodType.ANNUAL))
        assert periods.periods[0].year == 2023

    def test_period_index_labels(self):
        """Test PeriodIndex labels (line 152)."""
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        labels = periods.labels()
        assert labels == ["FY2024", "FY2025", "FY2026"]
