"""Independent policy for the dashboard projection sample window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo


@dataclass(frozen=True, slots=True)
class ProjectionWindow:
    """Shop-local dates used by one projection calculation."""

    as_of: date
    sample_start: date
    sample_end: date


@dataclass(frozen=True, slots=True)
class ProjectionPolicy:
    """Projection lookback and maturity policy, independent of reporting dates."""

    lookback_days: int = 30
    maturity_lag_days: int = 7

    def __post_init__(self) -> None:
        if self.lookback_days not in (30, 90):
            raise ValueError("projection_lookback_days must be 30 or 90")
        if self.maturity_lag_days < 0:
            raise ValueError("maturity_lag_days must be >= 0")

    def window(self, calculated_at: datetime, reporting_timezone: tzinfo) -> ProjectionWindow:
        if calculated_at.tzinfo is None:
            calculated_at = calculated_at.replace(tzinfo=UTC)
        local_today = calculated_at.astimezone(reporting_timezone).date()
        as_of = local_today - timedelta(days=1)
        sample_end = as_of - timedelta(days=self.maturity_lag_days)
        sample_start = sample_end - timedelta(days=self.lookback_days - 1)
        return ProjectionWindow(
            as_of=as_of,
            sample_start=sample_start,
            sample_end=sample_end,
        )
