"""Immutable daily payloads and pure retention/calendar transitions."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from app.end_users.days import midnight

DAY = timedelta(days=1)
REFRESHED = 1
UNAVAILABLE = 2
EXPIRED = 4
DAILY_KIND: Final = "attribution_daily"
DISPATCH_JOB = "schedule_attribution_daily"
DAY_JOB = "refresh_attribution_daily"


def iso_day(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("Expected an ISO calendar date")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Expected YYYY-MM-DD")
    return parsed


def horizon(payload: dict[str, Any]) -> date:
    if set(payload) != {"scheduled_period"}:
        raise ValueError("Daily dispatcher requires scheduled_period only")
    return iso_day(payload["scheduled_period"])


@dataclass(frozen=True)
class DailyPayload:
    project_id: UUID
    scheduled_period: date
    first_day: date
    last_day: date
    day: date
    dispatch_number: int

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "DailyPayload":
        if set(payload) != {
            "project_id",
            "scheduled_period",
            "first_day",
            "last_day",
            "day",
            "dispatch_number",
        }:
            raise ValueError("Daily child payload fields do not match the contract")
        number = payload["dispatch_number"]
        if type(number) is not int or not 0 < number < 2**63:
            raise ValueError("dispatch_number must be a positive bigint")
        item = cls(
            UUID(str(payload["project_id"])),
            iso_day(payload["scheduled_period"]),
            iso_day(payload["first_day"]),
            iso_day(payload["last_day"]),
            iso_day(payload["day"]),
            number,
        )
        if (
            item.first_day != item.scheduled_period - 7 * DAY
            or item.last_day != item.scheduled_period - 2 * DAY
            or not item.first_day <= item.day <= item.last_day
        ):
            raise ValueError("Daily child dates do not match its immutable period")
        return item

    def serialize(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "scheduled_period": self.scheduled_period.isoformat(),
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "day": self.day.isoformat(),
            "dispatch_number": self.dispatch_number,
        }


def latest_expired_period(cutoff: datetime, scheduled_horizon: date) -> date:
    """P is wholly expired exactly when midnight(P-1) <= cutoff."""
    return min(cutoff.date() + DAY, scheduled_horizon)


def expired_day(day: date, cutoff: datetime) -> bool:
    return midnight(day + DAY) <= cutoff
