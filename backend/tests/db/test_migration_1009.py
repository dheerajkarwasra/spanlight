"""Migration 1009 drops the two GIN indexes the app role can never use, and can be undone.

`traces_tags_idx` and `insights_evidence_trace_ids_idx` serve only `@>` / `&&`, which are not
leakproof, so under the tables' FORCE row-level security Postgres never turns them into index
conditions for the app role (see `tests/rls/test_index_usability.py`). Downgrade rebuilds both.
"""

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.migrations import alembic_config, downgrade_to, head_revision, upgrade_to_head

REVISION = "1009"
EXPECTED_DEFINITIONS = {
    "traces_tags_idx": "CREATE INDEX traces_tags_idx ON public.traces USING gin (tags)",
    "insights_evidence_trace_ids_idx": (
        "CREATE INDEX insights_evidence_trace_ids_idx ON public.insights "
        "USING gin (((evidence -> 'trace_ids'::text)) jsonb_path_ops)"
    ),
}


def _indexes(database_url: str) -> dict[str, tuple[str, bool]]:
    """Definition and validity of each index this migration manages, if it exists."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT c.relname, pg_get_indexdef(c.oid), i.indisvalid "
                    "FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
                    "WHERE c.relname = ANY (:names) "
                    "AND c.relnamespace = current_schema()::regnamespace"
                ),
                {"names": list(EXPECTED_DEFINITIONS)},
            )
            return {row[0]: (row[1], row[2]) for row in rows}
    finally:
        engine.dispose()


def _down_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION)
    assert script is not None and isinstance(script.down_revision, str)
    return script.down_revision


def test_1009_indexes_are_gone_at_head(app_database_url: str) -> None:
    assert head_revision() == REVISION
    assert _indexes(app_database_url) == {}


def test_1009_downgrade_rebuilds_both_indexes(app_database_url: str) -> None:
    try:
        downgrade_to(app_database_url, _down_revision())
        expected = {name: (definition, True) for name, definition in EXPECTED_DEFINITIONS.items()}
        assert _indexes(app_database_url) == expected

        upgrade_to_head(app_database_url)
        assert _indexes(app_database_url) == {}
    finally:
        upgrade_to_head(app_database_url)
