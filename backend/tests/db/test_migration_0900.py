"""Migration 0900 adds `spans.usage_rollup` and can be undone.

The flag is `boolean NOT NULL DEFAULT false`: a constant default, so the column add rewrites no
row, and every span stored before the upgrade reads as billable until a batch touches its trace.
"""

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.migrations import alembic_config, downgrade_to, head_revision, upgrade_to_head

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


def test_0900_round_trip(app_database_url: str) -> None:
    previous = ScriptDirectory.from_config(alembic_config()).get_revision("0900").down_revision
    assert isinstance(previous, str)
    assert _current_revision(app_database_url) == head_revision()
    assert _usage_rollup_column(app_database_url) == EXPECTED_COLUMN

    downgrade_to(app_database_url, previous)
    try:
        assert _current_revision(app_database_url) == previous
        assert _usage_rollup_column(app_database_url) is None
    finally:
        # The rest of the session shares this database, so it must end at head.
        upgrade_to_head(app_database_url)

    assert _current_revision(app_database_url) == head_revision()
    assert _usage_rollup_column(app_database_url) == EXPECTED_COLUMN
