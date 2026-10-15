"""Migration 0508 adds the cache-write usage columns and the price tiers, and can be undone.

Spans learn `cache_write_tokens` and `cache_write_1h_tokens` (the 1h subset of the 5m total, so
the schema refuses a subset larger than its total). Both price tables learn the two cache-write
rates and `min_prompt_tokens`, the lower bound of a price tier, which joins their unique keys so
one pattern can carry one row per tier. The unique constraints keep their names: the override
service names one of them in its `ON CONFLICT` clause.
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

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000508")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000508")
TRACE_ID = "0508" * 8

# (table, column) -> (udt_name, is_nullable, numeric precision, numeric scale, default)
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, int | None, int | None, str | None]] = {
    ("spans", "cache_write_tokens"): ("int4", "YES", 32, 0, None),
    ("spans", "cache_write_1h_tokens"): ("int4", "YES", 32, 0, None),
}
for _table in ("model_prices", "price_overrides"):
    EXPECTED_COLUMNS[(_table, "cache_write_5m_per_mtok")] = ("numeric", "YES", 12, 6, None)
    EXPECTED_COLUMNS[(_table, "cache_write_1h_per_mtok")] = ("numeric", "YES", 12, 6, None)
    EXPECTED_COLUMNS[(_table, "min_prompt_tokens")] = ("int4", "NO", 32, 0, "0")

EXPECTED_UNIQUE_KEYS = {
    ("model_prices", "model_prices_provider_model_pattern_effective_from_key"): (
        "UNIQUE (provider, model_pattern, effective_from, min_prompt_tokens)"
    ),
    ("price_overrides", "price_overrides_org_model_key"): (
        "UNIQUE (org_id, provider, model_pattern, effective_from, min_prompt_tokens)"
    ),
}


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
                      AND table_name IN ('spans', 'model_prices', 'price_overrides')
                    """
                )
            )
            return {(row[0], row[1]): (row[2], row[3], row[4], row[5], row[6]) for row in rows}
    finally:
        engine.dispose()


def _unique_keys(database_url: str) -> dict[tuple[str, str], str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE contype = 'u'
                      AND conrelid IN ('model_prices'::regclass, 'price_overrides'::regclass)
                    """
                )
            )
            return {(row[0], row[1]): row[2] for row in rows}
    finally:
        engine.dispose()


def _admin() -> psycopg.Connection:
    """The superuser sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(libpq_url, autocommit=True)


def _seed_trace(connection: psycopg.Connection) -> None:
    connection.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', 'org-0508')", (ORG_ID,)
    )
    connection.execute(
        "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'Proj', 'proj-0508')",
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
    cache_write: int | None,
    cache_write_1h: int | None,
) -> None:
    connection.execute(
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at, cache_write_tokens, cache_write_1h_tokens) "
        "VALUES (%s, %s, %s, 'llm', 'chat', 'ok', now(), now(), %s, %s)",
        (PROJECT_ID, TRACE_ID, span_id, cache_write, cache_write_1h),
    )


def _insert_price(
    connection: psycopg.Connection,
    min_prompt_tokens: int,
    cache_write_5m: Decimal | None = None,
    cache_write_1h: Decimal | None = None,
) -> None:
    connection.execute(
        "INSERT INTO model_prices (id, provider, model_pattern, input_per_mtok, output_per_mtok, "
        "effective_from, version, min_prompt_tokens, cache_write_5m_per_mtok, "
        "cache_write_1h_per_mtok) "
        "VALUES (%s, 'tier-test', 'tier-model', 1, 2, '2026-01-01T00:00:00Z', 'test', %s, %s, %s)",
        (uuid.uuid4(), min_prompt_tokens, cache_write_5m, cache_write_1h),
    )


def _insert_override(connection: psycopg.Connection, min_prompt_tokens: int) -> None:
    connection.execute(
        "INSERT INTO price_overrides (id, org_id, provider, model_pattern, input_per_mtok, "
        "output_per_mtok, effective_from, min_prompt_tokens) "
        "VALUES (%s, %s, 'tier-test', 'tier-model', 1, 2, '2026-01-01T00:00:00Z', %s)",
        (uuid.uuid4(), ORG_ID, min_prompt_tokens),
    )


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def test_0508_columns_and_keys_at_head(app_database_url: str) -> None:
    assert _current_revision(app_database_url) == head_revision()
    columns = _columns(app_database_url)
    for key, expected in EXPECTED_COLUMNS.items():
        assert columns.get(key) == expected, key
    assert _unique_keys(app_database_url) == EXPECTED_UNIQUE_KEYS


def test_0508_checks_reject_bad_rows() -> None:
    with _admin() as connection:
        _seed_trace(connection)
        # The 1h subset may equal its total, and either may be unknown.
        _insert_span(connection, "0508" * 4, 4, 4)
        _insert_span(connection, "0509" * 4, None, None)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_span(connection, "050a" * 4, 4, 5)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_span(connection, "050b" * 4, -1, None)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_span(connection, "050c" * 4, None, -1)

        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_price(connection, -1)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_price(connection, 0, cache_write_5m=Decimal("-1"))
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_price(connection, 0, cache_write_1h=Decimal("-1"))
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_override(connection, -1)


def test_0508_unique_keys_allow_one_row_per_tier() -> None:
    with _admin() as connection:
        _seed_trace(connection)
        try:
            _insert_price(connection, 0)
            _insert_price(connection, 200_000)
            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_price(connection, 200_000)
            _insert_override(connection, 0)
            _insert_override(connection, 200_000)
            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_override(connection, 0)
        finally:
            # `model_prices` is not truncated between tests and every price book loads all of
            # it; the override rows go with the organization.
            connection.execute("DELETE FROM model_prices WHERE provider = 'tier-test'")


async def test_0508_round_trip(
    app_database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    downgrade_to(app_database_url, "0507")
    try:
        assert _current_revision(app_database_url) == "0507"
        columns = _columns(app_database_url)
        assert not EXPECTED_COLUMNS.keys() & columns.keys()
        assert _unique_keys(app_database_url) == {
            ("model_prices", "model_prices_provider_model_pattern_effective_from_key"): (
                "UNIQUE (provider, model_pattern, effective_from)"
            ),
            ("price_overrides", "price_overrides_org_model_key"): (
                "UNIQUE (org_id, provider, model_pattern, effective_from)"
            ),
        }
    finally:
        # The rest of the session shares this database, so it must end at head.
        upgrade_to_head(app_database_url)
        async with session_factory() as db:
            await sync_seed_prices(db)
            await db.commit()

    assert _current_revision(app_database_url) == head_revision()
    columns = _columns(app_database_url)
    for key, expected in EXPECTED_COLUMNS.items():
        assert columns.get(key) == expected, key
    assert _unique_keys(app_database_url) == EXPECTED_UNIQUE_KEYS
