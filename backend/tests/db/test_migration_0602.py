"""Migration 0602 adds `cost_rollups_daily` and `user_stats_daily.computed_at`, and can be undone.

`cost_rollups_daily` holds one row per project, UTC day, provider, model and service tier, built
from spans by the daily cost rollup job and read by the reconciliation report. Its dimensions are
nullable, so its natural key is a unique expression index that treats a NULL dimension as the
empty string: two rows with every dimension NULL for the same project and day collide.

Every test here downgrades the session's shared database or writes rows under the truncated
`organizations`, and leaves the schema as it found it.
"""

import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.conftest import admin_database_url

ORG = uuid.UUID("10000000-0000-4000-8000-000000000602")
PROJECT = uuid.UUID("20000000-0000-4000-8000-000000000602")

TABLE = "cost_rollups_daily"
NATURAL_KEY = "cost_rollups_daily_natural_key"
PROJECT_DAY_INDEX = "cost_rollups_daily_project_id_day_idx"

COUNT_COLUMNS = (
    "calls",
    "priced_calls",
    "unpriced_calls",
    "usage_unreported_calls",
    "cut_stream_calls",
    "cache_hit_calls",
    "unmodelled_modifier_calls",
    "uncached_input_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "output_tokens",
    "web_search_requests",
)

# column -> (udt_name, is_nullable, default)
EXPECTED_COLUMNS: dict[str, tuple[str, str, str | None]] = {
    "id": ("int8", "NO", None),
    "project_id": ("uuid", "NO", None),
    "day": ("date", "NO", None),
    "provider": ("text", "YES", None),
    "provider_canonical": ("text", "YES", None),
    "model": ("text", "YES", None),
    "model_pattern": ("text", "YES", None),
    "service_tier": ("text", "YES", None),
    **{column: ("int8", "NO", None) for column in COUNT_COLUMNS},
    "cost_usd": ("numeric", "YES", None),
    "fee_usd": ("numeric", "YES", None),
    "computed_at": ("timestamptz", "NO", "now()"),
}


def _query(database_url: str, sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]
    finally:
        engine.dispose()


def _columns(database_url: str, table: str) -> dict[str, tuple[object, ...]]:
    rows = _query(
        database_url,
        "SELECT column_name, udt_name, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :table",
        table=table,
    )
    return {str(row[0]): row[1:] for row in rows}


def _policies(database_url: str, table: str = TABLE) -> dict[str, tuple[object, object]]:
    rows = _query(
        database_url,
        "SELECT policyname, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = :table",
        table=table,
    )
    return {str(row[0]): (row[1], row[2]) for row in rows}


def _indexes(database_url: str) -> dict[str, str]:
    rows = _query(
        database_url,
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = :table",
        table=TABLE,
    )
    return {str(row[0]): str(row[1]) for row in rows}


def _row_security(database_url: str) -> tuple[object, ...] | None:
    rows = _query(
        database_url,
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:table)",
        table=TABLE,
    )
    return rows[0] if rows else None


def _current_revision(database_url: str) -> object:
    return _query(database_url, "SELECT version_num FROM alembic_version")[0][0]


def _assert_at_head(database_url: str) -> None:
    assert _current_revision(database_url) == head_revision()
    assert _columns(database_url, TABLE) == EXPECTED_COLUMNS
    assert _row_security(database_url) == (True, True)
    # The policies are the exact expressions of every project table, as 0505 wrote them.
    evaluators = _policies(database_url, "evaluators")
    policies = _policies(database_url)
    assert policies == {
        "cost_rollups_daily_project_isolation": evaluators["evaluators_project_isolation"],
        "cost_rollups_daily_worker_bypass": evaluators["evaluators_worker_bypass"],
    }
    for qual, with_check in policies.values():
        assert qual == with_check
    indexes = _indexes(database_url)
    assert indexes[NATURAL_KEY] == (
        f"CREATE UNIQUE INDEX {NATURAL_KEY} ON public.{TABLE} USING btree (project_id, day, "
        "COALESCE(provider, ''::text), COALESCE(model, ''::text), "
        "COALESCE(service_tier, ''::text))"
    )
    assert indexes[PROJECT_DAY_INDEX] == (
        f"CREATE INDEX {PROJECT_DAY_INDEX} ON public.{TABLE} USING btree (project_id, day)"
    )
    computed_at = _columns(database_url, "user_stats_daily")["computed_at"]
    assert computed_at == ("timestamptz", "NO", "now()")


def test_0602_round_trip(app_database_url: str) -> None:
    _assert_at_head(app_database_url)

    downgrade_to(app_database_url, "0601")
    try:
        assert _current_revision(app_database_url) == "0601"
        assert _columns(app_database_url, TABLE) == {}
        assert _policies(app_database_url) == {}
        assert _row_security(app_database_url) is None
        assert "computed_at" not in _columns(app_database_url, "user_stats_daily")
    finally:
        # The rest of the session shares this database, so it must end at head. Nothing between
        # 0601 and 0602 touches the price tables, so the seed rows need no rebuilding here.
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)


def _admin() -> psycopg.Connection:
    """The superuser sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(libpq_url, autocommit=True)


def _insert_rollup(connection: psycopg.Connection, **dimensions: str | None) -> None:
    row: dict[str, object] = {
        "project_id": PROJECT,
        "day": "2026-10-15",
        "provider": None,
        "model": None,
        "service_tier": None,
        **{column: 0 for column in COUNT_COLUMNS},
        **dimensions,
    }
    columns = ", ".join(row)
    placeholders = ", ".join("%s" for _ in row)
    connection.execute(
        f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})", tuple(row.values())
    )


def test_natural_key_treats_null_dimensions_as_equal() -> None:
    with _admin() as connection:
        connection.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'A', 'org-0602')", (ORG,)
        )
        connection.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'A', 'proj-0602')",
            (PROJECT, ORG),
        )
        _insert_rollup(connection)
        with pytest.raises(psycopg.errors.UniqueViolation, match=NATURAL_KEY):
            _insert_rollup(connection)
        # A dimension that differs, even from NULL to a value, is another row.
        _insert_rollup(connection, provider="anthropic")
        _insert_rollup(connection, provider="anthropic", model="claude-sonnet-5-5")
        _insert_rollup(
            connection, provider="anthropic", model="claude-sonnet-5-5", service_tier="batch"
        )
        with pytest.raises(psycopg.errors.UniqueViolation, match=NATURAL_KEY):
            _insert_rollup(connection, provider="anthropic", model="claude-sonnet-5-5")
