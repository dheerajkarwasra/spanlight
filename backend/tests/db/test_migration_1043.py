"""Migration 1043 lets rollup token totals be unknown, and puts the zeros back on downgrade.

Failure mode: a stored 0 that stands for "no call reported usage" keeps showing "0 tokens" for
hours and user days no rollup pass rewrites again, or the backfill nulls a total some span did
report, or one whose spans retention already removed, or a 0 one of two spans reported once the
other span's trace is gone (retention records the hour in `aggregate_incomplete_hours`), or any
0 of a project whose source history was never verified; or it holds every row lock of a table in
one transaction and a killed run loses all its work. The test seeds rows at the 1042 shape,
upgrades, and checks that only provably unreported totals of verified, complete hours and days
became NULL (usage rollups do not count as reporting, as in the writers) and that each table's
rows were rewritten in more than one transaction (a trigger logs the transaction id of every
updated row); the downgrade restores 0 and NOT NULL.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from alembic.script import ScriptDirectory

from app.db.migrations import alembic_config
from tests.conftest import APP_ROLE
from tests.db import schema_probe as probe
from tests.db.migration_support import (
    admin_database_url,
    downgrade_to,
    expected_revision,
    upgrade_to_target,
)

REVISION = "1043"
BELOW = "1042"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001043")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001043")
# A second project whose rows push each backfill past its first batch.
SECOND_ORG_ID = uuid.UUID("30000000-0000-4000-8000-000000002043")
SECOND_PROJECT_ID = uuid.UUID("40000000-0000-4000-8000-000000002043")
# Projects whose zeros must stay: one lost a trace of the seeded hour to retention (recorded in
# `aggregate_incomplete_hours`), one never had its source history verified.
LOST_ORG_ID = uuid.UUID("50000000-0000-4000-8000-000000003043")
LOST_PROJECT_ID = uuid.UUID("60000000-0000-4000-8000-000000003043")
UNVERIFIED_ORG_ID = uuid.UUID("70000000-0000-4000-8000-000000004043")
UNVERIFIED_PROJECT_ID = uuid.UUID("80000000-0000-4000-8000-000000004043")
HOUR = datetime(2026, 10, 1, 9, tzinfo=UTC)
TABLES = ("span_rollups_hourly", "user_stats_daily")
# A 0 one span reported next to a span that reported nothing, with the reporting span gone.
KEPT_HOURLY = [(10, 0, 0)]
KEPT_USERS = [0]

# (model, stored input/output/cached at 1042, spans as (input, output, cached, usage_rollup))
HOURLY: list[tuple[str, tuple[int, int, int], list[tuple[int | None, ...]]]] = [
    ("a", (0, 0, 0), [(None, None, None, False)]),  # a failed call: no usage
    ("b", (10, 0, 0), [(10, 0, None, False)]),  # a reported 0 output stays 0
    ("c", (0, 0, 0), []),  # spans past retention: the stored value is kept
    ("d", (0, 0, 0), [(10, 5, 0, True)]),  # only a usage rollup, never summed
]
HOURLY_UPGRADED = [(None, None, None), (10, 0, None), (0, 0, 0), (None, None, None)]

# (user, stored tokens at 1042, llm spans as (input, output))
USERS: list[tuple[str, int, list[tuple[int | None, int | None]]]] = [
    ("u1", 0, [(None, None)]),
    ("u2", 0, [(0, 0)]),
    ("u3", 0, []),  # no trace left: kept
]
USERS_UPGRADED = [None, 0, 0]


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


def _span(
    connection: object,
    trace_id: str,
    index: int,
    model: str,
    values: tuple,
    project: uuid.UUID = PROJECT_ID,
) -> None:
    input_tokens, output_tokens, cached_tokens, rollup = values
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at, provider, model, input_tokens, output_tokens, cached_tokens, usage_rollup) "
        "VALUES (%s, %s, %s, 'llm', 'call', 'ok', %s, %s, 'openai', %s, %s, %s, %s, %s)",
        (
            project,
            trace_id,
            f"{trace_id[:14]}{index:02d}",
            HOUR,
            HOUR + timedelta(seconds=1),
            model,
            input_tokens,
            output_tokens,
            cached_tokens,
            rollup,
        ),
    )


def _trace(
    connection: object, trace_id: str, user: str | None = None, project: uuid.UUID = PROJECT_ID
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment, "
        "external_user_id) VALUES (%s, %s, %s, %s, 'prod', %s)",
        (project, trace_id, HOUR, HOUR + timedelta(seconds=1), user),
    )


def _hourly_batch() -> int:
    script = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION)
    assert script is not None
    return int(script.module.HOURLY_BATCH)


def _seed_at_1042() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        trace_id = "a" * 32
        _trace(connection, trace_id)
        index = 0
        for model, (input_total, output_total, cached_total), spans in HOURLY:
            connection.execute(
                "INSERT INTO span_rollups_hourly (project_id, bucket_start, environment, "
                "provider, model, kind, span_count, errors, input_tokens, output_tokens, "
                "cached_tokens, unpriced_calls, latency_buckets, ttft_buckets) VALUES "
                "(%s, %s, 'prod', 'openai', %s, 'llm', 1, 0, %s, %s, %s, 1, "
                "array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32]))",
                (PROJECT_ID, HOUR, model, input_total, output_total, cached_total),
            )
            for values in spans:
                _span(connection, trace_id, index, model, values)
                index += 1
        for number, (user, tokens, spans) in enumerate(USERS):
            if spans:
                user_trace = f"{number + 1}" * 32
                _trace(connection, user_trace, user)
                for offset, (input_tokens, output_tokens) in enumerate(spans):
                    _span(
                        connection,
                        user_trace,
                        offset,
                        "u",
                        (input_tokens, output_tokens, None, False),
                    )
            connection.execute(
                "INSERT INTO user_stats_daily (project_id, day, environment, external_user_id, "
                "traces, llm_calls, errors, unpriced_calls, tokens) "
                "VALUES (%s, %s, 'prod', %s, 1, 1, 0, 1, %s)",
                (PROJECT_ID, HOUR.date(), user, tokens),
            )
        _seed_second_project(connection, _hourly_batch())
        # The test database never ran `activate-aggregate-coverage`: verify these two from the
        # epoch, as a project created after a fresh activation is.
        connection.execute(
            "UPDATE projects SET aggregate_history_unverified_before = '1970-01-01 00:00:00+00' "
            "WHERE id = ANY(%s)",
            ([PROJECT_ID, SECOND_PROJECT_ID],),
        )
        _seed_kept_project(connection, LOST_ORG_ID, LOST_PROJECT_ID, lost_hour=True)
        _seed_kept_project(connection, UNVERIFIED_ORG_ID, UNVERIFIED_PROJECT_ID, lost_hour=False)


def _seed_kept_project(
    connection: psycopg.Connection, org_id: uuid.UUID, project_id: uuid.UUID, *, lost_hour: bool
) -> None:
    """`KEPT_HOURLY` and `KEPT_USERS` with only the span that reported nothing left. With
    `lost_hour` the hour is verified but recorded as lost to retention; without it the project's
    history is unverified (the column's 1042 default)."""
    probe.seed_project(connection, org_id, project_id)
    if lost_hour:
        connection.execute(
            "UPDATE projects SET aggregate_history_unverified_before = '1970-01-01 00:00:00+00' "
            "WHERE id = %s",
            (project_id,),
        )
        connection.execute(
            "INSERT INTO aggregate_incomplete_hours (project_id, bucket_hour, reason) "
            "VALUES (%s, %s, 'retention')",
            (project_id, HOUR),
        )
    trace_id = project_id.hex[:1] * 32
    _trace(connection, trace_id, "k", project_id)
    _span(connection, trace_id, 0, "k", (None, None, None, False), project_id)
    connection.execute(
        "INSERT INTO span_rollups_hourly (project_id, bucket_start, environment, provider, model, "
        "kind, span_count, errors, input_tokens, output_tokens, cached_tokens, unpriced_calls, "
        "latency_buckets, ttft_buckets) VALUES (%s, %s, 'prod', 'openai', 'k', 'llm', 2, 0, "
        "10, 0, 0, 1, array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32]))",
        (project_id, HOUR),
    )
    connection.execute(
        "INSERT INTO user_stats_daily (project_id, day, environment, external_user_id, traces, "
        "llm_calls, errors, unpriced_calls, tokens) VALUES (%s, %s, 'prod', 'k', 2, 2, 0, 1, 0)",
        (project_id, HOUR.date()),
    )


def _kept(project_id: uuid.UUID) -> tuple[list[tuple[object, ...]], list[object]]:
    hourly = probe.query(
        admin_database_url(),
        "SELECT input_tokens, output_tokens, cached_tokens FROM span_rollups_hourly "
        "WHERE project_id = :project_id",
        project_id=project_id,
    )
    users = probe.query(
        admin_database_url(),
        "SELECT tokens FROM user_stats_daily WHERE project_id = :project_id",
        project_id=project_id,
    )
    return hourly, [row[0] for row in users]


def _seed_second_project(connection: psycopg.Connection, hours: int) -> None:
    """`hours` unreported-zero hours (ids after the first project's, so the last ones fall in a
    second id range) and one unreported user day (a second project-day) in a second project."""
    probe.seed_project(connection, SECOND_ORG_ID, SECOND_PROJECT_ID)
    ended = HOUR + timedelta(seconds=1)
    connection.execute(
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment, "
        "external_user_id) VALUES (%s, %s, %s, %s, 'prod', NULL), (%s, %s, %s, %s, 'prod', 'u4')",
        (SECOND_PROJECT_ID, "b" * 32, HOUR, ended, SECOND_PROJECT_ID, "c" * 32, HOUR, ended),
    )
    connection.execute(
        "INSERT INTO span_rollups_hourly (project_id, bucket_start, environment, provider, model, "
        "kind, span_count, errors, input_tokens, output_tokens, cached_tokens, unpriced_calls, "
        "latency_buckets, ttft_buckets) "
        "SELECT %s, %s, 'prod', 'openai', 'm' || n, 'llm', 1, 0, 0, 0, 0, 1, "
        "array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32]) FROM generate_series(1, %s) AS n",
        (SECOND_PROJECT_ID, HOUR, hours),
    )
    connection.execute(
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at, provider, model, usage_rollup) "
        "SELECT %s, %s, lpad(to_hex(n), 16, '0'), 'llm', 'call', 'ok', %s, %s, 'openai', "
        "'m' || n, false FROM generate_series(1, %s) AS n",
        (SECOND_PROJECT_ID, "b" * 32, HOUR, ended, hours),
    )
    connection.execute(
        "INSERT INTO user_stats_daily (project_id, day, environment, external_user_id, traces, "
        "llm_calls, errors, unpriced_calls, tokens) VALUES (%s, %s, 'prod', 'u4', 1, 1, 0, 1, 0)",
        (SECOND_PROJECT_ID, HOUR.date()),
    )


def _install_xact_log() -> None:
    """Log the transaction id of every row the backfill updates, per table."""
    with probe.admin() as connection:
        connection.execute(
            "CREATE TABLE test_xact_log (table_name text NOT NULL, xid xid8 NOT NULL)"
        )
        connection.execute(f"GRANT INSERT ON test_xact_log TO {APP_ROLE}")
        connection.execute(
            "CREATE FUNCTION test_log_xact() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "INSERT INTO test_xact_log VALUES (TG_TABLE_NAME, pg_current_xact_id()); "
            "RETURN NULL; END $$"
        )
        for table in TABLES:
            connection.execute(
                f"CREATE TRIGGER test_log_xact AFTER UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION test_log_xact()"
            )


def _drop_xact_log() -> None:
    with probe.admin() as connection:
        connection.execute("DROP FUNCTION IF EXISTS test_log_xact() CASCADE")
        connection.execute("DROP TABLE IF EXISTS test_xact_log")


def _batched_tables() -> set[object]:
    """The tables whose rows the backfill updated in more than one transaction."""
    rows = probe.query(
        admin_database_url(),
        "SELECT table_name FROM test_xact_log GROUP BY table_name HAVING count(DISTINCT xid) > 1",
    )
    return {row[0] for row in rows}


def _second_project_zeros_left() -> list[tuple[object, ...]]:
    return probe.query(
        admin_database_url(),
        "SELECT count(*) FROM span_rollups_hourly "
        "WHERE project_id = :project_id AND input_tokens IS NOT NULL "
        "UNION ALL SELECT count(*) FROM user_stats_daily "
        "WHERE project_id = :project_id AND tokens IS NOT NULL",
        project_id=SECOND_PROJECT_ID,
    )


def _hourly() -> list[tuple[object, ...]]:
    return probe.query(
        admin_database_url(),
        "SELECT input_tokens, output_tokens, cached_tokens FROM span_rollups_hourly "
        "WHERE project_id = :project_id ORDER BY model",
        project_id=PROJECT_ID,
    )


def _users() -> list[object]:
    rows = probe.query(
        admin_database_url(),
        "SELECT tokens FROM user_stats_daily WHERE project_id = :project_id "
        "ORDER BY external_user_id",
        project_id=PROJECT_ID,
    )
    return [row[0] for row in rows]


def _nullable(database_url: str) -> set[object]:
    found: set[object] = set()
    for table, names in (
        ("span_rollups_hourly", ("input_tokens", "output_tokens", "cached_tokens")),
        ("attribution_rollups_daily", ("input_tokens", "output_tokens")),
        ("user_stats_daily", ("tokens",)),
    ):
        columns = probe.columns(database_url, table)
        found |= {columns[name][1] for name in names}
    return found


@pytest.mark.migration_revision("1043")
def test_1043_nulls_only_unreported_rollup_totals_and_restores_zeros(
    migration_database_url: str, back_to_target: None
) -> None:
    downgrade_to(migration_database_url, BELOW)
    _seed_at_1042()
    _install_xact_log()
    try:
        upgrade_to_target(migration_database_url)
        assert _hourly() == HOURLY_UPGRADED
        assert _users() == USERS_UPGRADED
        assert _second_project_zeros_left() == [(0,), (0,)]
        assert _kept(LOST_PROJECT_ID) == (KEPT_HOURLY, KEPT_USERS)
        assert _kept(UNVERIFIED_PROJECT_ID) == (KEPT_HOURLY, KEPT_USERS)
        assert _nullable(migration_database_url) == {"YES"}
        assert _batched_tables() == set(TABLES)
    finally:
        _drop_xact_log()

    downgrade_to(migration_database_url, BELOW)
    assert _hourly() == [(0, 0, 0), (10, 0, 0), (0, 0, 0), (0, 0, 0)]
    assert _users() == [0, 0, 0]
    assert _nullable(migration_database_url) == {"NO"}
