"""Migration 1034: `users.weekly_digest_opt_out`, false for every existing and new user."""

from collections.abc import Iterator

import pytest

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1033"
COLUMN = "weekly_digest_opt_out"


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


@pytest.mark.migration_revision("1034")
def test_migration_1034_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    udt, nullable, _, _, default = probe.columns(url, "users")[COLUMN]
    assert (udt, nullable, default) == ("bool", "NO", "false")

    downgrade_to(url, BELOW)
    assert COLUMN not in probe.columns(url, "users")

    upgrade_to_target(url)
    assert COLUMN in probe.columns(url, "users")
