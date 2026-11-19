"""
Tests for period remaining uncovered lines - next/prev quarterly/monthly wrap.
Target lines: 76, 80, 88, 92
"""

from financial_model.core.period import Period, PeriodType


class TestPeriodWrapCoverage:
    """Test Period next/prev wrap for quarterly and monthly (lines 76, 80, 88, 92)."""

    def test_period_next_quarterly_wraps_year(self):
        """Test next() for Q4 wraps to next year Q1 (line 76)."""
        p = Period(year=2024, quarter=4, period_type=PeriodType.QUARTERLY)
        next_p = p.next()
        assert next_p.year == 2025
        assert next_p.quarter == 1

    def test_period_next_monthly_wraps_year(self):
        """Test next() for month 12 wraps to next year month 1 (line 80)."""
        p = Period(year=2024, month=12, period_type=PeriodType.MONTHLY)
        next_p = p.next()
        assert next_p.year == 2025
        assert next_p.month == 1

    def test_period_prev_quarterly_wraps_year(self):
        """Test prev() for Q1 wraps to previous year Q4 (line 88)."""
        p = Period(year=2024, quarter=1, period_type=PeriodType.QUARTERLY)
        prev_p = p.prev()
        assert prev_p.year == 2023
        assert prev_p.quarter == 4

    def test_period_prev_monthly_wraps_year(self):
        """Test prev() for month 1 wraps to previous year month 12 (line 92)."""
        p = Period(year=2024, month=1, period_type=PeriodType.MONTHLY)
        prev_p = p.prev()
        assert prev_p.year == 2023
        assert prev_p.month == 12
