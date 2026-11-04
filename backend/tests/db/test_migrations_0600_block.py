"""The whole 0600 block (span billing classes, billing sources, cost rollups) can be undone.

Downgrading from head to 0508 removes every migration of the block in one run, and upgrading
again rebuilds exactly the schema the session started with.
"""

import pytest

from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target
from tests.db.test_migrations import _current_revision, _schema_snapshot


@pytest.mark.migration_revision("0602")
def test_0600_block_round_trips(migration_database_url: str) -> None:
    before = _schema_snapshot(migration_database_url)
    assert _current_revision(migration_database_url) == expected_revision()

    downgrade_to(migration_database_url, "0508")
    try:
        assert _current_revision(migration_database_url) == "0508"
    finally:
        # Complete the round-trip contract at its pinned upper revision.
        upgrade_to_target(migration_database_url)

    assert _current_revision(migration_database_url) == expected_revision()
    assert _schema_snapshot(migration_database_url) == before
