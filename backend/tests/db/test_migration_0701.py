"""Migration 0701 adds `error_rollups_hourly`, and can be undone.

One row per project, hour, environment, provider, model, error class and rate-limit kind. Its
dimensions may be NULL, and the unique key is `NULLS NOT DISTINCT`, so two rows for the same
unknown dimensions collide instead of both being stored.

The round trip downgrades the session's shared database and leaves it at head.
"""

import uuid

import psycopg
import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

TABLE = "error_rollups_hourly"
KEY = "error_rollups_hourly_dimensions_key"
INDEX = "error_rollups_hourly_project_bucket_idx"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000701")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000701")

EXPECTED_COLUMNS = {
    "id": ("int8", "NO", 64, 0, None),
    "project_id": ("uuid", "NO", None, None, None),
    "bucket_start": ("timestamptz", "NO", None, None, None),
    "environment": ("text", "YES", None, None, None),
    "provider": ("text", "YES", None, None, None),
    "model": ("text", "YES", None, None, None),
    "error_class": ("text", "NO", None, None, None),
    "rate_limit_kind": ("text", "YES", None, None, None),
    "errors": ("int8", "NO", 64, 0, None),
    "with_retry_after": ("int8", "NO", 64, 0, None),
    "computed_at": ("timestamptz", "NO", None, None, "now()"),
}


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == head_revision()
    assert probe.columns(database_url, TABLE) == EXPECTED_COLUMNS
    probe.assert_project_policies(database_url, TABLE)
    assert probe.constraints(database_url, TABLE)[KEY] == (
        "UNIQUE NULLS NOT DISTINCT (project_id, bucket_start, environment, provider, model, "
        "error_class, rate_limit_kind)",
        True,
    )
    assert probe.indexes(database_url, TABLE)[INDEX] == (
        f"CREATE INDEX {INDEX} ON public.{TABLE} USING btree (project_id, bucket_start)"
    )


def _insert(connection: psycopg.Connection) -> None:
    connection.execute(
        f"INSERT INTO {TABLE} (project_id, bucket_start, error_class, errors, with_retry_after) "
        "VALUES (%s, '2026-10-16T00:00:00Z', 'rate_limit', 1, 0)",
        (PROJECT_ID,),
    )


def test_0701_round_trip(app_database_url: str) -> None:
    _assert_at_head(app_database_url)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _insert(connection)
        # Every dimension but the class is NULL, and NULLs are not distinct in this key.
        with pytest.raises(psycopg.errors.UniqueViolation, match=KEY):
            _insert(connection)

    downgrade_to(app_database_url, "0700")
    try:
        assert probe.current_revision(app_database_url) == "0700"
        assert probe.row_security(app_database_url, TABLE) is None
        assert probe.policies(app_database_url, TABLE) == {}
    finally:
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)
