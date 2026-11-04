"""Migration 1039: the per-project ingest cap overrides and the `ingest_quota_days` counter.

The three override columns on `projects` are nullable and refuse `0` (unlimited for one project
is a large number, never an accident). `ingest_quota_days` is project-scoped and under row-level
security with the two policies of every project table. The round trip uses an explicitly owned
database at the pinned historical revision.
"""

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1038"
TABLE = "ingest_quota_days"
OVERRIDES = ("ingest_spans_per_second", "ingest_spans_per_day", "ingest_bytes_per_day")
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001039")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001039")

COLUMNS = {
    "project_id": ("uuid", "NO", None, None, None),
    "day": ("date", "NO", None, None, None),
    "spans": ("int8", "NO", 64, 0, "0"),
    "bytes": ("int8", "NO", 64, 0, "0"),
}


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


def _assert_at_head(url: str) -> None:
    assert probe.columns(url, TABLE) == COLUMNS
    probe.assert_project_policies(url, TABLE)
    constraints = probe.constraints(url, TABLE)
    assert constraints["ingest_quota_days_pkey"] == ("PRIMARY KEY (project_id, day)", True)
    assert {"ingest_quota_days_spans_check", "ingest_quota_days_bytes_check"} <= constraints.keys()
    projects = probe.columns(url, "projects")
    assert projects["ingest_spans_per_second"] == ("int4", "YES", 32, 0, None)
    assert projects["ingest_spans_per_day"] == ("int8", "YES", 64, 0, None)
    assert projects["ingest_bytes_per_day"] == ("int8", "YES", 64, 0, None)


def _set_override(connection: psycopg.Connection, column: str, value: int) -> None:
    connection.execute(f"UPDATE projects SET {column} = %s WHERE id = %s", (value, PROJECT_ID))


@pytest.mark.migration_revision("1039")
def test_migration_1039_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    _assert_at_head(url)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        for column in OVERRIDES:
            _set_override(connection, column, 1)
            with pytest.raises(psycopg.errors.CheckViolation, match=column):
                _set_override(connection, column, 0)
        connection.execute(
            f"INSERT INTO {TABLE} (project_id, day) VALUES (%s, '2026-11-02')", (PROJECT_ID,)
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="spans_check"):
            connection.execute(
                f"UPDATE {TABLE} SET spans = -1 WHERE project_id = %s", (PROJECT_ID,)
            )

    downgrade_to(url, BELOW)
    assert probe.row_security(url, TABLE) is None
    assert not set(OVERRIDES) & probe.columns(url, "projects").keys()

    upgrade_to_target(url)
    _assert_at_head(url)
