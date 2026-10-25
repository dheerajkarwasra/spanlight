"""Migration 1020 adds the partial index on scores by project and source.

Failure mode: an interrupted `CREATE INDEX CONCURRENTLY` leaves an INVALID index that
`IF NOT EXISTS` would keep forever, so the checklist would never use it. The test marks the index
INVALID at 1019's shape plus the leftover, upgrades again, and checks it was rebuilt valid.

Every test here leaves the session's shared database at head.
"""

from collections.abc import Iterator

import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

BELOW = "1019"
INDEX = "scores_project_source_idx"
DEFINITION = (
    f"CREATE INDEX {INDEX} ON public.scores USING btree (project_id, source) "
    "WHERE (source <> 'api'::score_source)"
)


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_head(app_database_url)
    assert probe.current_revision(app_database_url) == head_revision()


def _is_valid(database_url: str) -> object:
    rows = probe.query(
        database_url,
        "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
        "WHERE c.relname = :name",
        name=INDEX,
    )
    return rows[0][0] if rows else None


def test_migration_1020_round_trip(app_database_url: str, back_to_head: None) -> None:
    assert probe.indexes(app_database_url, "scores")[INDEX] == DEFINITION
    downgrade_to(app_database_url, BELOW)
    assert INDEX not in probe.indexes(app_database_url, "scores")
    upgrade_to_head(app_database_url)
    assert probe.indexes(app_database_url, "scores")[INDEX] == DEFINITION
    assert _is_valid(app_database_url) is True


def test_upgrade_rebuilds_an_invalid_leftover(app_database_url: str, back_to_head: None) -> None:
    downgrade_to(app_database_url, BELOW)
    with probe.admin() as connection:
        # What an interrupted concurrent build leaves behind.
        connection.execute(f"CREATE INDEX {INDEX} ON scores (project_id, source)")
        connection.execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass", (INDEX,)
        )
    assert _is_valid(app_database_url) is False

    upgrade_to_head(app_database_url)

    assert _is_valid(app_database_url) is True
    assert probe.indexes(app_database_url, "scores")[INDEX] == DEFINITION
