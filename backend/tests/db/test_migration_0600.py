"""Migration 0600 adds the span billing classes and `server_tool_fees`, and can be undone.

Spans learn `service_tier` (one of four provider tiers), `web_search_requests` and `fee_usd`, the
non-token part of `cost_usd`; each has a CHECK under its own name, so an insert that breaks one
names it. `server_tool_fees` is the global, versioned price list for server-side tools: one row
per provider, tool and effective date, with the tool name limited to the tools priced today.

Every test here downgrades the session's shared database or writes rows outside the truncated
tables, and leaves both as it found them.
"""

import uuid
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.pricing.cost import sync_seed_prices
from tests.conftest import admin_database_url

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000600")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000600")
TRACE_ID = "0600" * 8
EFFECTIVE_FROM = "2026-10-16T00:00:00Z"

# (table, column) -> (udt_name, is_nullable, numeric precision, numeric scale, default)
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, int | None, int | None, str | None]] = {
    ("spans", "service_tier"): ("text", "YES", None, None, None),
    ("spans", "web_search_requests"): ("int4", "YES", 32, 0, None),
    ("spans", "fee_usd"): ("numeric", "YES", 14, 8, None),
    ("server_tool_fees", "id"): ("uuid", "NO", None, None, None),
    ("server_tool_fees", "provider"): ("text", "NO", None, None, None),
    ("server_tool_fees", "tool"): ("text", "NO", None, None, None),
    ("server_tool_fees", "usd_per_1k_requests"): ("numeric", "NO", 12, 6, None),
    ("server_tool_fees", "effective_from"): ("timestamptz", "NO", None, None, None),
    ("server_tool_fees", "version"): ("text", "NO", None, None, None),
}

EXPECTED_SPAN_CHECKS = {
    "spans_service_tier_check": (
        "CHECK ((service_tier = ANY (ARRAY['standard'::text, 'batch'::text, "
        "'priority'::text, 'flex'::text])))"
    ),
    "spans_web_search_requests_check": "CHECK ((web_search_requests >= 0))",
    "spans_fee_usd_check": "CHECK ((fee_usd >= (0)::numeric))",
}
FEES_UNIQUE_KEY = "server_tool_fees_provider_tool_effective_from_key"


def _columns(database_url: str) -> dict[tuple[str, str], tuple[str, str, int, int, str | None]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name, udt_name, is_nullable,
                           numeric_precision, numeric_scale, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN ('spans', 'server_tool_fees')
                    """
                )
            )
            return {(row[0], row[1]): (row[2], row[3], row[4], row[5], row[6]) for row in rows}
    finally:
        engine.dispose()


def _constraints(database_url: str, table: str) -> dict[str, tuple[str, bool]]:
    """Every CHECK and UNIQUE of `table`: name -> (definition, validated)."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT conname, pg_get_constraintdef(oid), convalidated
                    FROM pg_constraint
                    WHERE contype IN ('c', 'u') AND conrelid = to_regclass(:table)
                    """
                ),
                {"table": table},
            )
            return {row[0]: (row[1], row[2]) for row in rows}
    finally:
        engine.dispose()


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def _admin() -> psycopg.Connection:
    """The superuser sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(libpq_url, autocommit=True)


def _seed_trace(connection: psycopg.Connection) -> None:
    connection.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', 'org-0600')", (ORG_ID,)
    )
    connection.execute(
        "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'Proj', 'proj-0600')",
        (PROJECT_ID, ORG_ID),
    )
    connection.execute(
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
        "VALUES (%s, %s, now(), now())",
        (PROJECT_ID, TRACE_ID),
    )


def _insert_span(
    connection: psycopg.Connection,
    span_id: str,
    service_tier: str | None = None,
    web_search_requests: int | None = None,
    fee_usd: Decimal | None = None,
) -> None:
    connection.execute(
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at, service_tier, web_search_requests, fee_usd) "
        "VALUES (%s, %s, %s, 'llm', 'chat', 'ok', now(), now(), %s, %s, %s)",
        (PROJECT_ID, TRACE_ID, span_id, service_tier, web_search_requests, fee_usd),
    )


def _insert_fee(
    connection: psycopg.Connection,
    tool: str = "web_search",
    usd_per_1k_requests: Decimal = Decimal("10"),
    provider: str = "fee-test",
) -> None:
    connection.execute(
        "INSERT INTO server_tool_fees (id, provider, tool, usd_per_1k_requests, effective_from, "
        "version) VALUES (%s, %s, %s, %s, %s, 'test')",
        (uuid.uuid4(), provider, tool, usd_per_1k_requests, EFFECTIVE_FROM),
    )


def _assert_at_head(database_url: str) -> None:
    assert _current_revision(database_url) == head_revision()
    columns = _columns(database_url)
    for key, expected in EXPECTED_COLUMNS.items():
        assert columns.get(key) == expected, key
    span_checks = _constraints(database_url, "spans")
    for name, definition in EXPECTED_SPAN_CHECKS.items():
        assert span_checks.get(name) == (definition, True), name
    fee_constraints = _constraints(database_url, "server_tool_fees")
    assert fee_constraints[FEES_UNIQUE_KEY] == ("UNIQUE (provider, tool, effective_from)", True)
    assert fee_constraints["server_tool_fees_tool_check"] == (
        "CHECK ((tool = 'web_search'::text))",
        True,
    )
    assert fee_constraints["server_tool_fees_usd_per_1k_requests_check"] == (
        "CHECK ((usd_per_1k_requests >= (0)::numeric))",
        True,
    )
    assert fee_constraints["server_tool_fees_provider_check"] == (
        "CHECK ((provider = lower(provider)))",
        True,
    )


def test_0600_columns_and_constraints_at_head(app_database_url: str) -> None:
    _assert_at_head(app_database_url)


def test_0600_checks_reject_bad_rows() -> None:
    with _admin() as connection:
        _seed_trace(connection)
        # Every tier is accepted, and each column may be unknown.
        for index, tier in enumerate(("standard", "batch", "priority", "flex")):
            _insert_span(connection, f"06{index:02d}" * 4, tier, 0, Decimal("0"))
        _insert_span(connection, "0610" * 4, None, None, None)
        with pytest.raises(psycopg.errors.IntegrityError, match="spans_service_tier_check"):
            _insert_span(connection, "0611" * 4, service_tier="fast")
        with pytest.raises(psycopg.errors.IntegrityError, match="spans_web_search_requests_check"):
            _insert_span(connection, "0612" * 4, web_search_requests=-1)
        with pytest.raises(psycopg.errors.IntegrityError, match="spans_fee_usd_check"):
            _insert_span(connection, "0613" * 4, fee_usd=Decimal("-0.01"))


def test_0600_server_tool_fees_refuses_unknown_tools_and_duplicates() -> None:
    with _admin() as connection:
        try:
            _insert_fee(connection)
            with pytest.raises(psycopg.errors.IntegrityError, match="server_tool_fees_tool_check"):
                _insert_fee(connection, tool="code_execution")
            with pytest.raises(
                psycopg.errors.IntegrityError, match="server_tool_fees_usd_per_1k_requests_check"
            ):
                _insert_fee(connection, usd_per_1k_requests=Decimal("-1"))
            with pytest.raises(psycopg.errors.IntegrityError, match=FEES_UNIQUE_KEY):
                _insert_fee(connection)
        finally:
            # The table is not truncated between tests.
            connection.execute("DELETE FROM server_tool_fees WHERE provider = 'fee-test'")


async def test_0600_round_trip(
    app_database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    downgrade_to(app_database_url, "0508")
    try:
        assert _current_revision(app_database_url) == "0508"
        columns = _columns(app_database_url)
        assert not EXPECTED_COLUMNS.keys() & columns.keys()
        assert not EXPECTED_SPAN_CHECKS.keys() & _constraints(app_database_url, "spans").keys()
        assert _constraints(app_database_url, "server_tool_fees") == {}
    finally:
        # The rest of the session shares this database, so it must end at head.
        upgrade_to_head(app_database_url)
        async with session_factory() as db:
            await sync_seed_prices(db)
            await db.commit()

    _assert_at_head(app_database_url)
    # The rebuilt table keeps the stored form of `provider` the only form.
    with (
        _admin() as connection,
        pytest.raises(psycopg.errors.IntegrityError, match="server_tool_fees_provider_check"),
    ):
        _insert_fee(connection, provider="Anthropic")


async def test_0600_is_idempotent_on_rerun(
    app_database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An interrupted run leaves the span columns and checks behind; the next run completes."""
    downgrade_to(app_database_url, "0508")
    try:
        with _admin() as connection:
            connection.execute(
                "ALTER TABLE spans ADD COLUMN service_tier text, "
                "ADD COLUMN web_search_requests integer, ADD COLUMN fee_usd numeric(14, 8), "
                "ADD CONSTRAINT spans_fee_usd_check CHECK (fee_usd >= 0) NOT VALID"
            )
            # A row written through the half-applied schema is still checked.
            _seed_trace(connection)
            _insert_span(connection, "0620" * 4, "batch", 2, Decimal("0.02"))
        upgrade_to_head(app_database_url)
    finally:
        upgrade_to_head(app_database_url)
        async with session_factory() as db:
            await sync_seed_prices(db)
            await db.commit()

    _assert_at_head(app_database_url)
