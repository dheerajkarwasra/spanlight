"""Migration 1047 adds the partial index of the traces that have errors.

Failure mode: an interrupted `CREATE INDEX CONCURRENTLY` leaves an INVALID index that
`IF NOT EXISTS` would keep forever, so the errors-only list would never use it. The test leaves
such an index behind, upgrades again, and checks it was rebuilt valid.
"""

from collections.abc import Iterator

import pytest

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1046"
INDEX = "traces_project_errored_started_idx"
DEFINITION = (
    f"CREATE INDEX {INDEX} ON public.traces "
    "USING btree (project_id, started_at DESC, trace_id DESC) WHERE (error_count > 0)"
)


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


def _is_valid(database_url: str) -> object:
    rows = probe.query(
        database_url,
        "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
        "WHERE c.relname = :name",
        name=INDEX,
    )
    return rows[0][0] if rows else None


@pytest.mark.migration_revision("1047")
def test_migration_1047_round_trip(migration_database_url: str, back_to_target: None) -> None:
    assert probe.indexes(migration_database_url, "traces")[INDEX] == DEFINITION
    downgrade_to(migration_database_url, BELOW)
    assert INDEX not in probe.indexes(migration_database_url, "traces")
    upgrade_to_target(migration_database_url)
    assert probe.indexes(migration_database_url, "traces")[INDEX] == DEFINITION
    assert _is_valid(migration_database_url) is True


@pytest.mark.migration_revision("1047")
def test_upgrade_rebuilds_an_invalid_leftover(
    migration_database_url: str, back_to_target: None
) -> None:
    downgrade_to(migration_database_url, BELOW)
    with probe.admin() as connection:
        # What an interrupted concurrent build leaves behind.
        connection.execute(f"CREATE INDEX {INDEX} ON traces (project_id, started_at)")
        connection.execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass", (INDEX,)
        )
    assert _is_valid(migration_database_url) is False

    upgrade_to_target(migration_database_url)

    assert _is_valid(migration_database_url) is True
    assert probe.indexes(migration_database_url, "traces")[INDEX] == DEFINITION
