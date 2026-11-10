"""
Period management for financial models.
Supports annual, quarterly, monthly periods with flexible date ranges.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Self
import pandas as pd


class PeriodType(Enum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"


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
        elif self.period_type == PeriodType.QUARTERLY:
            return date(self.year, (self.quarter - 1) * 3 + 1, 1)
        else:
            return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        if self.period_type == PeriodType.ANNUAL:
            return date(self.year, 12, 31)
        elif self.period_type == PeriodType.QUARTERLY:
            q_end_month = self.quarter * 3
            if q_end_month == 12:
                return date(self.year, 12, 31)
            next_q_start = date(self.year, q_end_month + 1, 1)
            from datetime import timedelta
            return next_q_start - timedelta(days=1)
        else:
            from calendar import monthrange
            _, last_day = monthrange(self.year, self.month)
            return date(self.year, self.month, last_day)

    @property
    def label(self) -> str:
        if self.period_type == PeriodType.ANNUAL:
            return f"FY{self.year}"
        elif self.period_type == PeriodType.QUARTERLY:
            return f"Q{self.quarter} FY{self.year}"
        else:
            return f"{self.month:02d}/{self.year}"

    def next(self) -> Self:
        if self.period_type == PeriodType.ANNUAL:
            return Period(year=self.year + 1, period_type=PeriodType.ANNUAL)
        elif self.period_type == PeriodType.QUARTERLY:
            if self.quarter == 4:
                return Period(year=self.year + 1, quarter=1, period_type=PeriodType.QUARTERLY)
            return Period(year=self.year, quarter=self.quarter + 1, period_type=PeriodType.QUARTERLY)
        else:
            if self.month == 12:
                return Period(year=self.year + 1, month=1, period_type=PeriodType.MONTHLY)
            return Period(year=self.year, month=self.month + 1, period_type=PeriodType.MONTHLY)

    def prev(self) -> Self:
        if self.period_type == PeriodType.ANNUAL:
            return Period(year=self.year - 1, period_type=PeriodType.ANNUAL)
        elif self.period_type == PeriodType.QUARTERLY:
            if self.quarter == 1:
                return Period(year=self.year - 1, quarter=4, period_type=PeriodType.QUARTERLY)
            return Period(year=self.year, quarter=self.quarter - 1, period_type=PeriodType.QUARTERLY)
        else:
            if self.month == 1:
                return Period(year=self.year - 1, month=12, period_type=PeriodType.MONTHLY)
            return Period(year=self.year, month=self.month - 1, period_type=PeriodType.MONTHLY)

    def to_timestamp(self) -> pd.Timestamp:
        return pd.Timestamp(self.end_date)

    @classmethod
    def from_date(cls, dt: date, period_type: PeriodType = PeriodType.ANNUAL) -> Self:
        if period_type == PeriodType.ANNUAL:
            return cls(year=dt.year, period_type=PeriodType.ANNUAL)
        elif period_type == PeriodType.QUARTERLY:
            quarter = (dt.month - 1) // 3 + 1
            return cls(year=dt.year, quarter=quarter, period_type=PeriodType.QUARTERLY)
        else:
            return cls(year=dt.year, month=dt.month, period_type=PeriodType.MONTHLY)

    @classmethod
    def range(
        cls,
        start: Self,
        end: Self,
        inclusive: bool = True,
    ) -> list[Self]:
        periods = []
        current = start
        while current <= end if inclusive else current < end:
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

    def __iter__(self):
        return iter(self.periods)

    def __contains__(self, period: Period) -> bool:
        return period in self.periods

    def add(self, period: Period) -> Self:
        if period not in self.periods:
            self.periods.append(period)
            self.periods.sort()
        return self

    def to_dataframe_index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([p.to_timestamp() for p in self.periods])

    def labels(self) -> list[str]:
        return [p.label for p in self.periods]