"""The review policy's calendar: UTC days, the nightly hour, and which days a manual run may
select. Pure."""

from datetime import UTC, date, datetime, timedelta

from app.review.errors import DayNotCompleteError, DayTooOldError

SELECTION_HOUR_UTC = 1
MAX_MANUAL_DAYS_BACK = 7


def day_window(day: date) -> tuple[datetime, datetime]:
    """`[00:00 UTC of the day, 00:00 UTC of the next day)`."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def previous_day(now: datetime) -> date:
    """The UTC day before `now`'s: the one the nightly run selects."""
    return now.astimezone(UTC).date() - timedelta(days=1)


def next_run_at(now: datetime, *, enabled: bool) -> datetime | None:
    """The next `SELECTION_HOUR_UTC` strictly after `now`, or `None` for a disabled policy."""
    if not enabled:
        return None
    now = now.astimezone(UTC)
    run_at = now.replace(hour=SELECTION_HOUR_UTC, minute=0, second=0, microsecond=0)
    return run_at if run_at > now else run_at + timedelta(days=1)


def check_manual_day(day: date, now: datetime) -> None:
    """A manual run covers a completed UTC day at most `MAX_MANUAL_DAYS_BACK` days back.

    Raises `DayNotCompleteError` for today or later and `DayTooOldError` beyond the limit.
    """
    today = now.astimezone(UTC).date()
    if day >= today:
        raise DayNotCompleteError
    if day < today - timedelta(days=MAX_MANUAL_DAYS_BACK):
        raise DayTooOldError
