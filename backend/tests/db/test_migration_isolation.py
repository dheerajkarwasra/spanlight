"""Historical failures cannot alter the live-test database or its activation state."""

from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import _libpq
from tests.conftest import admin_database_url as shared_admin_database_url
from tests.db import schema_probe as probe
from tests.db.migration_support import active_target, downgrade_to, upgrade_to_target
from tests.db.test_migration_validate_under_rls import _seed_project, _seed_span


def _parent_state() -> tuple[object, ...]:
    with psycopg.connect(_libpq(shared_admin_database_url())) as db:
        return (
            db.execute(
                "SELECT id, name, aggregate_history_unverified_before FROM projects ORDER BY id"
            ).fetchall(),
            db.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'projects' "
                "AND column_name = 'aggregate_history_unverified_before'"
            ).fetchone(),
            db.execute("SELECT row_to_json(p) FROM model_prices p ORDER BY id").fetchall(),
            db.execute("SELECT row_to_json(p) FROM server_tool_fees p ORDER BY id").fetchall(),
            db.execute("SELECT version_num FROM alembic_version").fetchone(),
        )


@pytest.mark.migration_revision("0401")
async def test_failed_and_successful_historical_round_trips_leave_parent_unchanged(
    app_database_url: str,
    migration_database_url: str,
    migration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sentinel = uuid4()
    with psycopg.connect(_libpq(shared_admin_database_url())) as parent:
        probe.seed_project(parent, sentinel, sentinel)
    before = _parent_state()
    with pytest.raises(ValueError, match="active owned database"):
        downgrade_to(app_database_url, "base")
    with pytest.raises(ValueError, match="active owned database"):
        upgrade_to_target(app_database_url)
    # Both synchronous legacy probes and async sessions must address the same owned target.
    with probe.admin() as historical:
        expected_database = historical.execute("SELECT current_database()").fetchone()
    async with migration_session_factory() as db:
        assert (await db.scalar(text("SELECT current_database()")),) == expected_database
    downgrade_to(migration_database_url, "0400")
    _seed_project()
    with probe.admin() as historical:
        historical.execute("ALTER TABLE spans ADD COLUMN request_hash text")
    _seed_span(", request_hash", ("NOT-A-HASH",))
    with pytest.raises(exc.IntegrityError):
        upgrade_to_target(migration_database_url)
    assert _parent_state() == before
    with probe.admin() as historical:
        historical.execute("UPDATE spans SET request_hash = NULL")
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == "0401"
    assert _parent_state() == before


def test_historical_mutations_require_active_owned_context(app_database_url: str) -> None:
    assert active_target.get() is None
    with pytest.raises(ValueError, match="active owned database"):
        downgrade_to(app_database_url, "base")
    with pytest.raises(ValueError, match="active owned database"):
        upgrade_to_target(app_database_url)
