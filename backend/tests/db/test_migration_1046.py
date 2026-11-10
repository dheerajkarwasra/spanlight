"""Migration 1046: `jobs` is vacuumed and analyzed at 2 % dead rows, not the default 20 %."""

from collections.abc import Iterator

import pytest

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1045"
OPTIONS = ["autovacuum_vacuum_scale_factor=0.02", "autovacuum_analyze_scale_factor=0.02"]


def _options(url: str) -> object:
    [(options,)] = probe.query(url, "SELECT reloptions FROM pg_class WHERE oid = 'jobs'::regclass")
    return options


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


@pytest.mark.migration_revision("1046")
def test_migration_1046_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    assert _options(url) == OPTIONS

    downgrade_to(url, BELOW)
    assert _options(url) is None

    upgrade_to_target(url)
    assert _options(url) == OPTIONS
