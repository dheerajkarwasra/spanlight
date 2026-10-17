"""The whole 0600 block (span billing classes, billing sources, cost rollups) can be undone.

Downgrading from head to 0508 removes every migration of the block in one run, and upgrading
again rebuilds exactly the schema the session started with.
"""

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db.test_migrations import _current_revision, _schema_snapshot


def test_0600_block_round_trips(app_database_url: str) -> None:
    before = _schema_snapshot(app_database_url)
    assert _current_revision(app_database_url) == head_revision()

    downgrade_to(app_database_url, "0508")
    try:
        assert _current_revision(app_database_url) == "0508"
    finally:
        # The rest of the session shares this database, so it must end at head. The downgrade
        # dropped `server_tool_fees`; the autouse fixture of this package seeds it again.
        upgrade_to_head(app_database_url)

    assert _current_revision(app_database_url) == head_revision()
    assert _schema_snapshot(app_database_url) == before
