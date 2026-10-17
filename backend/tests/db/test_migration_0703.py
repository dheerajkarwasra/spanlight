"""Migration 0703 adds the telemetry loss counters, and can be undone.

`telemetry_losses_hourly` counts what was lost on the way in, per project, hour, source and
reason; `sdk_exporter_counters` keeps the cumulative counters each SDK exporter reports. Both
are project-scoped and under row-level security.

The round trip downgrades the session's shared database and leaves it at head.
"""

import uuid

import psycopg
import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

LOSSES = "telemetry_losses_hourly"
COUNTERS = "sdk_exporter_counters"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000703")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000703")

LOSS_COLUMNS = {
    "id": ("int8", "NO", 64, 0, None),
    "project_id": ("uuid", "NO", None, None, None),
    "bucket_start": ("timestamptz", "NO", None, None, None),
    "source": ("text", "NO", None, None, None),
    "reason": ("text", "NO", None, None, None),
    "count": ("int8", "NO", 64, 0, None),
}
COUNTER_COLUMNS = {
    "project_id": ("uuid", "NO", None, None, None),
    "exporter_id": ("text", "NO", None, None, None),
    "dropped_total": ("int8", "NO", 64, 0, None),
    "failed_total": ("int8", "NO", 64, 0, None),
    "sdk": ("text", "NO", None, None, None),
    "sdk_version": ("text", "NO", None, None, None),
    "last_seen_at": ("timestamptz", "NO", None, None, None),
}


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == head_revision()
    assert probe.columns(database_url, LOSSES) == LOSS_COLUMNS
    assert probe.columns(database_url, COUNTERS) == COUNTER_COLUMNS
    probe.assert_project_policies(database_url, LOSSES)
    probe.assert_project_policies(database_url, COUNTERS)
    losses = probe.constraints(database_url, LOSSES)
    assert losses["telemetry_losses_hourly_key"] == (
        "UNIQUE (project_id, bucket_start, source, reason)",
        True,
    )
    assert {"telemetry_losses_hourly_source_check", "telemetry_losses_hourly_count_check"} <= (
        losses.keys()
    )
    counters = probe.constraints(database_url, COUNTERS)
    assert counters["sdk_exporter_counters_pkey"] == ("PRIMARY KEY (project_id, exporter_id)", True)
    assert "sdk_exporter_counters_exporter_id_check" in counters


def _insert_loss(connection: psycopg.Connection, source: str, count: int) -> None:
    connection.execute(
        f"INSERT INTO {LOSSES} (project_id, bucket_start, source, reason, count) "
        "VALUES (%s, '2026-10-16T00:00:00Z', %s, 'queue_full', %s)",
        (PROJECT_ID, source, count),
    )


def _insert_counter(connection: psycopg.Connection, exporter_id: str) -> None:
    connection.execute(
        f"INSERT INTO {COUNTERS} (project_id, exporter_id, dropped_total, failed_total, sdk, "
        "sdk_version, last_seen_at) VALUES (%s, %s, 0, 0, 'spanlight-python', '0.2.0', now())",
        (PROJECT_ID, exporter_id),
    )


def test_0703_round_trip(app_database_url: str) -> None:
    _assert_at_head(app_database_url)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _insert_loss(connection, "sdk", 0)
        with pytest.raises(psycopg.errors.CheckViolation, match="count_check"):
            _insert_loss(connection, "gateway", -1)
        with pytest.raises(psycopg.errors.CheckViolation, match="source_check"):
            _insert_loss(connection, "otlp", 1)
        _insert_counter(connection, "0" * 32)
        with pytest.raises(psycopg.errors.CheckViolation, match="exporter_id_check"):
            _insert_counter(connection, "a" * 31)
        with pytest.raises(psycopg.errors.CheckViolation, match="exporter_id_check"):
            _insert_counter(connection, "A" * 32)

    downgrade_to(app_database_url, "0702")
    try:
        assert probe.current_revision(app_database_url) == "0702"
        assert probe.row_security(app_database_url, LOSSES) is None
        assert probe.row_security(app_database_url, COUNTERS) is None
    finally:
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)
