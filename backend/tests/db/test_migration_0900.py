"""Migration 0900 adds `spans.usage_rollup` and can be undone.

The flag is `boolean NOT NULL DEFAULT false`: a constant default, so the column add rewrites no
row, and every span stored before the upgrade reads as billable until a batch touches its trace.
"""

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.migrations import alembic_config
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

EXPECTED_COLUMN = ("bool", "NO", "false")


def _usage_rollup_column(database_url: str) -> tuple[str, str, str | None] | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT udt_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'spans'
                      AND column_name = 'usage_rollup'
                    """
                )
            ).one_or_none()
            return None if row is None else (row[0], row[1], row[2])
    finally:
        engine.dispose()


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


@pytest.mark.migration_revision("0900")
def test_0900_round_trip(migration_database_url: str) -> None:
    previous = ScriptDirectory.from_config(alembic_config()).get_revision("0900").down_revision
    assert isinstance(previous, str)
    assert _current_revision(migration_database_url) == expected_revision()
    assert _usage_rollup_column(migration_database_url) == EXPECTED_COLUMN

    downgrade_to(migration_database_url, previous)
    try:
        assert _current_revision(migration_database_url) == previous
        assert _usage_rollup_column(migration_database_url) is None
    finally:
        # Complete the round-trip contract at its pinned upper revision.
        upgrade_to_target(migration_database_url)

    assert _current_revision(migration_database_url) == expected_revision()
    assert _usage_rollup_column(migration_database_url) == EXPECTED_COLUMN
