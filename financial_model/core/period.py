"""
Period management for financial models.
Supports annual, quarterly, monthly periods with flexible date ranges.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

import pandas as pd


class PeriodType(Enum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"

    @property
    def periods_per_year(self) -> int:
        return {PeriodType.ANNUAL: 1, PeriodType.QUARTERLY: 4, PeriodType.MONTHLY: 12}[self]


@dataclass(frozen=True, order=True)
class Period:
    """Immutable period representation with start/end dates."""

    year: int
    quarter: int | None = None
    month: int | None = None
    period_type: PeriodType = PeriodType.ANNUAL

    def __post_init__(self) -> None:
        if self.period_type == PeriodType.QUARTERLY and self.quarter is None:
            raise ValueError("Quarter required for quarterly periods")
        if self.period_type == PeriodType.MONTHLY and self.month is None:
            raise ValueError("Month required for monthly periods")
        if self.period_type == PeriodType.ANNUAL:
            object.__setattr__(self, "quarter", None)
            object.__setattr__(self, "month", None)

    @property
    def start_date(self) -> date:
        if self.period_type == PeriodType.ANNUAL:
            return date(self.year, 1, 1)
        if self.period_type == PeriodType.QUARTERLY:
            assert self.quarter is not None
            return date(self.year, (self.quarter - 1) * 3 + 1, 1)
        assert self.month is not None
        return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        if self.period_type == PeriodType.ANNUAL:
            return date(self.year, 12, 31)
        if self.period_type == PeriodType.QUARTERLY:
            assert self.quarter is not None
            q_end_month = self.quarter * 3
            if q_end_month == 12:
                return date(self.year, 12, 31)
            next_q_start = date(self.year, q_end_month + 1, 1)
            return next_q_start - timedelta(days=1)
        assert self.month is not None
        _, last_day = monthrange(self.year, self.month)
        return date(self.year, self.month, last_day)

    @property
    def label(self) -> str:
        if self.period_type == PeriodType.ANNUAL:
            return f"FY{self.year}"
        if self.period_type == PeriodType.QUARTERLY:
            return f"Q{self.quarter} FY{self.year}"
        return f"{self.month:02d}/{self.year}"

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def next(self) -> Period:
        if self.period_type == PeriodType.ANNUAL:
            return Period(year=self.year + 1, period_type=PeriodType.ANNUAL)
        if self.period_type == PeriodType.QUARTERLY:
            assert self.quarter is not None
            if self.quarter == 4:
                return Period(year=self.year + 1, quarter=1, period_type=PeriodType.QUARTERLY)
            return Period(year=self.year, quarter=self.quarter + 1, period_type=PeriodType.QUARTERLY)
        assert self.month is not None
        if self.month == 12:
            return Period(year=self.year + 1, month=1, period_type=PeriodType.MONTHLY)
        return Period(year=self.year, month=self.month + 1, period_type=PeriodType.MONTHLY)

    def prev(self) -> Period:
        if self.period_type == PeriodType.ANNUAL:
            return Period(year=self.year - 1, period_type=PeriodType.ANNUAL)
        if self.period_type == PeriodType.QUARTERLY:
            assert self.quarter is not None
            if self.quarter == 1:
                return Period(year=self.year - 1, quarter=4, period_type=PeriodType.QUARTERLY)
            return Period(year=self.year, quarter=self.quarter - 1, period_type=PeriodType.QUARTERLY)
        assert self.month is not None
        if self.month == 1:
            return Period(year=self.year - 1, month=12, period_type=PeriodType.MONTHLY)
        return Period(year=self.year, month=self.month - 1, period_type=PeriodType.MONTHLY)

    def to_timestamp(self) -> pd.Timestamp:
        return pd.Timestamp(self.end_date)

    @classmethod
    def from_date(cls, dt: date, period_type: PeriodType = PeriodType.ANNUAL) -> Period:
        if period_type == PeriodType.ANNUAL:
            return cls(year=dt.year, period_type=PeriodType.ANNUAL)
        if period_type == PeriodType.QUARTERLY:
            quarter = (dt.month - 1) // 3 + 1
            return cls(year=dt.year, quarter=quarter, period_type=PeriodType.QUARTERLY)
        return cls(year=dt.year, month=dt.month, period_type=PeriodType.MONTHLY)

    @classmethod
    def range(cls, start: Period, end: Period, inclusive: bool = True) -> list[Period]:
        periods = []
        current = start
        while (current <= end) if inclusive else (current < end):
            periods.append(current)
            current = current.next()
        return periods


@dataclass
class PeriodIndex:
    """Manages a collection of periods with fast lookup."""

    periods: list[Period] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.periods = sorted(set(self.periods))

    def __len__(self) -> int:
        return len(self.periods)

    def __getitem__(self, idx: int) -> Period:
        return self.periods[idx]

    def __iter__(self) -> Iterator[Period]:
        return iter(self.periods)

    def __contains__(self, period: object) -> bool:
        return period in self.periods

    def add(self, period: Period) -> PeriodIndex:
        if period not in self.periods:
            self.periods.append(period)
            self.periods.sort()
        return self

    @classmethod
    def generate(
        cls,
        start_year: int,
        years: int,
        period_type: PeriodType | str = PeriodType.ANNUAL,
        count: int | None = None,
    ) -> PeriodIndex:
        """
        Build ``years`` years of consecutive periods starting on 1 January ``start_year``.
        ``count`` overrides the total number of periods (e.g. 6 quarters).
        """
        period_type = PeriodType(period_type) if isinstance(period_type, str) else period_type
        total = count if count is not None else years * period_type.periods_per_year
        if period_type == PeriodType.ANNUAL:
            first = Period(year=start_year, period_type=period_type)
        elif period_type == PeriodType.QUARTERLY:
            first = Period(year=start_year, quarter=1, period_type=period_type)
        else:
            first = Period(year=start_year, month=1, period_type=period_type)
        periods = []
        current = first
        for _ in range(total):
            periods.append(current)
            current = current.next()
        return cls(periods)

    @property
    def period_type(self) -> PeriodType:
        return self.periods[0].period_type if self.periods else PeriodType.ANNUAL

    def to_dataframe_index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([p.to_timestamp() for p in self.periods])

    def labels(self) -> list[str]:
        return [p.label for p in self.periods]
