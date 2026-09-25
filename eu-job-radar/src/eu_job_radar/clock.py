"""Controllable time so tests and the demo never depend on today's date."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

NOW_ENV = "EU_JOB_RADAR_NOW"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FixedClock:
    instant: datetime

    def __post_init__(self):
        if self.instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")

    def now(self) -> datetime:
        return self.instant.astimezone(UTC)

    def advance(self, **delta) -> None:
        from datetime import timedelta

        self.instant = self.instant + timedelta(**delta)


def from_environment():
    """Use EU_JOB_RADAR_NOW (an ISO datetime with offset) when set."""
    value = os.environ.get(NOW_ENV)
    if not value:
        return SystemClock()
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError(
            f"{NOW_ENV} must include a UTC offset, for example 2026-10-01T09:00:00+01:00"
        )
    return FixedClock(instant)


def iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
