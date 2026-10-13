"""Which backups the retention job deletes, and what a bad database URL may say.

Failure modes pinned here: retention deletes the last good backup (a long run of failed backups
would leave the bucket empty), keeps the wrong daily or Sunday backup, or deletes an object it
did not write; an error about a malformed backup database URL quotes the password.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.backups.retention import (
    DAILY_KEEP_DAYS,
    WEEKLY_KEEP_WEEKS,
    backup_key,
    parse_backup_key,
    prune_backups,
)
from app.backups.service import connection_params
from app.storage.object_store import ObjectInfo

# A Tuesday; the Sunday before it is 2026-10-11.
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)
SECRET = "S3cr3tPassw0rd"


def _objects(*moments: datetime) -> list[ObjectInfo]:
    return [ObjectInfo(backup_key(moment), 1, moment) for moment in moments]


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_the_newest_backup_is_kept_even_when_every_backup_is_past_the_policy() -> None:
    old = NOW - timedelta(days=400)
    moments = [old, old + timedelta(hours=1), old + timedelta(days=1)]

    deleted = prune_backups(_objects(*moments), NOW)

    assert deleted == sorted(backup_key(moment) for moment in moments[:2])


def test_the_newest_backup_of_each_of_the_last_14_days_and_of_8_sundays_is_kept() -> None:
    today = NOW.date()
    days = [today - timedelta(days=offset) for offset in range(90)]
    moments = [_at(day, hour) for day in days for hour in (1, 13)]
    last_sundays = {
        day for day in days if day.weekday() == 6 and (today - day).days < WEEKLY_KEEP_WEEKS * 7
    }
    expected_kept = {
        backup_key(_at(day, 13))
        for day in days
        if (today - day).days < DAILY_KEEP_DAYS or day in last_sundays
    }

    deleted = set(prune_backups(_objects(*moments), NOW))

    assert len(last_sundays) == WEEKLY_KEEP_WEEKS
    assert deleted == {backup_key(moment) for moment in moments} - expected_kept
    assert len(expected_kept) == DAILY_KEEP_DAYS + WEEKLY_KEEP_WEEKS - 2  # two Sundays overlap


@pytest.mark.parametrize(
    "key",
    [
        "backups/2026/10/13/other.dump",
        "backups/2026/10/12/spanlight-20261013T010000Z.dump",  # directory disagrees with stamp
        "backups/2026/13/40/spanlight-20261340T010000Z.dump",  # not a date
        "backups/2026/10/13/spanlight-20261013T010000Z.dump.partial",
        "exports/2025/01/01/spanlight-20250101T010000Z.dump",
    ],
)
def test_objects_this_code_did_not_write_are_never_deleted(key: str) -> None:
    old_backups = _objects(NOW - timedelta(days=300), NOW - timedelta(days=299))
    stranger = ObjectInfo(key, 1, NOW - timedelta(days=500))

    deleted = prune_backups([stranger, *old_backups], NOW)

    assert parse_backup_key(key) is None
    assert deleted == [old_backups[0].key]


def test_backups_stamped_after_the_clock_are_kept() -> None:
    # Clock skew between hosts: neither the newest nor a daily one, kept rather than guessed about.
    ahead = [NOW + timedelta(days=1), NOW + timedelta(days=2)]
    old = NOW - timedelta(days=300)

    assert prune_backups(_objects(old, *ahead), NOW) == [backup_key(old)]


def test_a_backup_key_round_trips_in_utc() -> None:
    taken = datetime(2026, 10, 13, 1, 30, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    key = backup_key(taken)

    assert key == "backups/2026/10/12/spanlight-20261012T200005Z.dump"
    assert parse_backup_key(key) == taken


def test_a_database_url_maps_to_libpq_settings() -> None:
    params = connection_params(
        f"postgresql+psycopg://u:{SECRET}@db.internal:5433/app?sslmode=require"
    )

    assert params == {
        "host": "db.internal",
        "port": "5433",
        "user": "u",
        "password": SECRET,
        "dbname": "app",
        "sslmode": "require",
    }


@pytest.mark.parametrize(
    "url",
    [
        f"mysql://u:{SECRET}@h/db",
        "not a url",
        f"postgresql://u:{SECRET}@h/db?options={SECRET}",
        f"postgresql://u:{SECRET}@h/db?sslmode=a&sslmode=b",
        f"postgresql://u:{SECRET}/db",  # the host is missing, so the password reads as the port
        f"postgresql://u:{SECRET}@h:5:6/db",
    ],
)
def test_a_bad_database_url_is_refused_without_quoting_it(url: str) -> None:
    with pytest.raises(ValueError, match="the database URL") as raised:
        connection_params(url)

    assert SECRET not in str(raised.value)
    # A logged traceback must not show the original error (which can quote the URL) either.
    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
