"""Migration 0700 adds the provider error fields and the retry columns, and can be undone.

Spans learn `provider_error_type`, `provider_error_code` and `rate_limit_kind` (a CHECK on its
seven values), and `error_class` accepts `gateway`. Traces learn `retry_calls` and
`retry_cost_usd`, NULL until the trace is recomputed. A downgrade deletes the spans of class
`gateway`, since the 0400 CHECK it restores refuses them; that loss is documented.

Every test here downgrades the session's shared database and leaves it at head.
"""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import exc

from alembic import command
from app.db.migrations import alembic_config, downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

BELOW = "0602"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000700")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000700")
TRACE_ID = "0700" * 8

SPAN_COLUMNS = {
    "provider_error_type": ("text", "YES", None, None, None),
    "provider_error_code": ("text", "YES", None, None, None),
    "rate_limit_kind": ("text", "YES", None, None, None),
}
TRACE_COLUMNS = {
    "retry_calls": ("int4", "YES", 32, 0, None),
    "retry_cost_usd": ("numeric", "YES", 14, 8, None),
}
SPAN_CHECKS = ("spans_rate_limit_kind_check", "spans_error_class_check")


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_head(app_database_url)
    assert probe.current_revision(app_database_url) == head_revision()


def _seed(connection: psycopg.Connection) -> None:
    probe.seed_project(connection, ORG_ID, PROJECT_ID)
    connection.execute(
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
        "VALUES (%s, %s, now(), now())",
        (PROJECT_ID, TRACE_ID),
    )


def _insert_span(connection: psycopg.Connection, span_id: str, **values: object) -> None:
    row: dict[str, object] = {"status": "error", **values}
    names = "".join(f", {name}" for name in row)
    placeholders = ", %s" * len(row)
    connection.execute(
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, started_at, ended_at"
        f"{names}) VALUES (%s, %s, %s, 'llm', 'chat', now(), now(){placeholders})",
        (PROJECT_ID, TRACE_ID, span_id, *row.values()),
    )


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == head_revision()
    spans = probe.columns(database_url, "spans")
    traces = probe.columns(database_url, "traces")
    assert {name: spans.get(name) for name in SPAN_COLUMNS} == SPAN_COLUMNS
    assert {name: traces.get(name) for name in TRACE_COLUMNS} == TRACE_COLUMNS
    checks = probe.constraints(database_url, "spans")
    for name in SPAN_CHECKS:
        assert checks[name][1] is True, name  # validated, not left NOT VALID
    assert "'gateway'::text" in checks["spans_error_class_check"][0]


def test_0700_round_trip(app_database_url: str, back_to_head: None) -> None:
    _assert_at_head(app_database_url)
    with probe.admin() as connection:
        _seed(connection)
        _insert_span(connection, "07000000000000a1", rate_limit_kind="spend_cap")
        with pytest.raises(psycopg.errors.CheckViolation, match="spans_rate_limit_kind_check"):
            _insert_span(connection, "07000000000000a2", rate_limit_kind="burst")
        _insert_span(connection, "07000000000000a3", error_class="gateway")

    downgrade_to(app_database_url, BELOW)
    assert probe.current_revision(app_database_url) == BELOW
    assert not SPAN_COLUMNS.keys() & probe.columns(app_database_url, "spans").keys()
    assert not TRACE_COLUMNS.keys() & probe.columns(app_database_url, "traces").keys()
    checks = probe.constraints(app_database_url, "spans")
    assert "spans_rate_limit_kind_check" not in checks
    assert checks["spans_error_class_check"][1] is True
    with (
        probe.admin() as connection,
        pytest.raises(psycopg.errors.CheckViolation, match="spans_error_class_check"),
    ):
        _insert_span(connection, "07000000000000a4", error_class="gateway")

    upgrade_to_head(app_database_url)
    _assert_at_head(app_database_url)


def test_0700_downgrade_deletes_gateway_class_rows(
    app_database_url: str, back_to_head: None
) -> None:
    with probe.admin() as connection:
        _seed(connection)
        _insert_span(connection, "07000000000000b1", error_class="gateway")
        _insert_span(connection, "07000000000000b2", error_class="timeout")
        _insert_span(connection, "07000000000000b3", status="ok")

    downgrade_to(app_database_url, BELOW)

    with probe.admin() as connection:
        rows = connection.execute(
            "SELECT span_id FROM spans WHERE project_id = %s ORDER BY span_id", (PROJECT_ID,)
        ).fetchall()
    assert [row[0] for row in rows] == ["07000000000000b2", "07000000000000b3"]


def test_0700_validates_under_rls(app_database_url: str, back_to_head: None) -> None:
    downgrade_to(app_database_url, BELOW)
    with probe.admin() as connection:
        _seed(connection)
        # An interrupted earlier run left the columns behind (its first statement, verbatim, so
        # the column order matches a clean upgrade), and a row was written into one of them.
        connection.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS provider_error_type text, "
            "ADD COLUMN IF NOT EXISTS provider_error_code text, "
            "ADD COLUMN IF NOT EXISTS rate_limit_kind text"
        )
    # The row is written by the app role under a project binding, so only that binding (or the
    # bypass) sees it; the migration runs with neither.
    with psycopg.connect(app_database_url.replace("+psycopg", "")) as connection:
        connection.execute("SELECT set_config('app.project_id', %s, true)", (str(PROJECT_ID),))
        _insert_span(connection, "07000000000000c1", rate_limit_kind="burst")

    with pytest.raises(exc.IntegrityError) as raised:
        command.upgrade(alembic_config(app_database_url), "0700")
    assert isinstance(raised.value.orig, psycopg.errors.CheckViolation)
    assert probe.current_revision(app_database_url) == BELOW

    with probe.admin() as connection:
        connection.execute(
            "UPDATE spans SET rate_limit_kind = NULL WHERE project_id = %s", (PROJECT_ID,)
        )
    command.upgrade(alembic_config(app_database_url), "0700")
    assert probe.current_revision(app_database_url) == "0700"
