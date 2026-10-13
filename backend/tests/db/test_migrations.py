"""Every migration can be undone, and doing so then redoing it rebuilds the same schema.

This protects every later migration: a `downgrade()` that forgets an index, a constraint or an
enum type is found here instead of during a rollback in production.
"""

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.db.models import Base
from app.pricing.cost import sync_seed_prices

# Each query lists one kind of schema object in a stable order, so two schemas that match
# compare equal and a difference names the object that is missing or changed.
_SNAPSHOT_QUERIES = {
    "columns": """
        SELECT table_name, column_name, udt_name, is_nullable, column_default,
               generation_expression
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """,
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1",
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        ORDER BY 1, 2
    """,
    "policies": """
        SELECT tablename, policyname, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY 1, 2
    """,
    "row_security": """
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
        ORDER BY 1
    """,
    "functions": """
        SELECT proname, pg_get_function_identity_arguments(oid), prosecdef, proconfig,
               -- NULL is the default ACL; a GRANT elsewhere (ensure_app_role) spells it out.
               COALESCE(proacl, acldefault('f', proowner))::text, prosrc
        FROM pg_proc AS f
        WHERE pronamespace = 'public'::regnamespace
          -- Functions of an extension (citext) belong to it, not to a migration.
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS d WHERE d.objid = f.oid AND d.deptype = 'e'
          )
        ORDER BY 1, 2
    """,
    "enums": """
        SELECT t.typname, e.enumlabel
        FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typnamespace = 'public'::regnamespace
        ORDER BY t.typname, e.enumsortorder
    """,
}


def _schema_snapshot(database_url: str) -> dict[str, list[Any]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                name: [tuple(row) for row in connection.execute(text(query))]
                for name, query in _SNAPSHOT_QUERIES.items()
            }
    finally:
        engine.dispose()


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


async def test_migrations_downgrade_to_base_and_upgrade_to_head(
    app_database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    before = _schema_snapshot(app_database_url)
    assert _current_revision(app_database_url) == head_revision()

    downgrade_to(app_database_url, "base")
    try:
        emptied = _schema_snapshot(app_database_url)
        # Only Alembic's own bookkeeping may remain, and no enum type may be left behind.
        assert {row[0] for row in emptied["columns"]} == {"alembic_version"}
        assert emptied["enums"] == []
        assert emptied["policies"] == []
        assert emptied["functions"] == []
    finally:
        # The rest of the session shares this database, so it must end at head even when an
        # assertion above fails. The price table is seeded once per session by a fixture and
        # was dropped with the schema, so it is seeded again here.
        upgrade_to_head(app_database_url)
        async with session_factory() as db:
            await sync_seed_prices(db)
            await db.commit()

    assert _current_revision(app_database_url) == head_revision()
    after = _schema_snapshot(app_database_url)
    assert after == before
    tables = {row[0] for row in after["columns"]}
    assert set(Base.metadata.tables) <= tables
