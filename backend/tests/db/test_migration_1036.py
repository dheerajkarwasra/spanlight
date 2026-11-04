"""Migration 1036: `alert_states.clear_since`, a nullable timestamp, and its round trip."""

from collections.abc import Iterator

import pytest

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1035"
COLUMN = "clear_since"


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


@pytest.mark.migration_revision("1036")
def test_migration_1036_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    udt, nullable, _, _, default = probe.columns(url, "alert_states")[COLUMN]
    assert (udt, nullable, default) == ("timestamptz", "YES", None)

    downgrade_to(url, BELOW)
    assert COLUMN not in probe.columns(url, "alert_states")

    upgrade_to_target(url)
    assert COLUMN in probe.columns(url, "alert_states")
