"""Migration 0702 adds the global provider incident tables, and can be undone.

`provider_incidents` keeps the incidents the providers' status pages publish, and
`provider_status_sources` one row per status feed with its polling state, seeded for the three
adapters. Neither table holds tenant data, so neither carries row-level security.

The round trip downgrades the session's shared database and leaves it at head.
"""

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

INCIDENTS = "provider_incidents"
SOURCES = "provider_status_sources"

INCIDENT_COLUMNS = {
    "id": ("uuid", "NO", None, None, None),
    "provider": ("text", "NO", None, None, None),
    "source": ("text", "NO", None, None, None),
    "external_id": ("text", "NO", None, None, None),
    "title": ("text", "NO", None, None, None),
    "impact": ("text", "NO", None, None, None),
    "status": ("text", "NO", None, None, None),
    "api_affected": ("bool", "NO", None, None, None),
    "components": ("_text", "NO", None, None, "'{}'::text[]"),
    "started_at": ("timestamptz", "NO", None, None, None),
    "resolved_at": ("timestamptz", "YES", None, None, None),
    "url": ("text", "NO", None, None, None),
    "latest_update": ("text", "YES", None, None, None),
    "feed_updated_at": ("timestamptz", "YES", None, None, None),
    "first_seen_at": ("timestamptz", "NO", None, None, "now()"),
    "fetched_at": ("timestamptz", "NO", None, None, None),
}
SOURCE_COLUMNS = {
    "provider": ("text", "NO", None, None, None),
    "url": ("text", "NO", None, None, None),
    "etag": ("text", "YES", None, None, None),
    "last_modified": ("text", "YES", None, None, None),
    "last_polled_at": ("timestamptz", "YES", None, None, None),
    "last_success_at": ("timestamptz", "YES", None, None, None),
    "last_outcome": ("text", "YES", None, None, None),
    "consecutive_failures": ("int4", "NO", 32, 0, "0"),
    "last_error_type": ("text", "YES", None, None, None),
}
INCIDENT_CONSTRAINTS = {
    "provider_incidents_provider_check",
    "provider_incidents_source_check",
    "provider_incidents_impact_check",
    "provider_incidents_status_check",
    "provider_incidents_provider_external_id_key",
}
INCIDENT_INDEXES = {
    "provider_incidents_provider_started_idx": "(provider, started_at DESC)",
    "provider_incidents_started_idx": "(started_at DESC)",
}
SEEDED = [
    ("anthropic", "https://status.claude.com/api/v2/incidents.json", None),
    ("google", "https://status.cloud.google.com/incidents.json", None),
    ("openai", "https://status.openai.com/api/v2/incidents.json", None),
]


def _sources(database_url: str) -> list[tuple[object, ...]]:
    return probe.query(
        database_url, f"SELECT provider, url, last_polled_at FROM {SOURCES} ORDER BY provider"
    )


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == head_revision()
    assert probe.columns(database_url, INCIDENTS) == INCIDENT_COLUMNS
    assert probe.columns(database_url, SOURCES) == SOURCE_COLUMNS
    constraints = probe.constraints(database_url, INCIDENTS)
    assert constraints.keys() >= INCIDENT_CONSTRAINTS
    assert all(validated for _, validated in constraints.values())
    indexes = probe.indexes(database_url, INCIDENTS)
    for name, columns in INCIDENT_INDEXES.items():
        assert indexes[name].endswith(f"USING btree {columns}"), name
    assert _sources(database_url) == SEEDED
    for table in (INCIDENTS, SOURCES):
        assert probe.row_security(database_url, table) == (False, False)
        assert probe.policies(database_url, table) == {}


def test_0702_round_trip(app_database_url: str) -> None:
    _assert_at_head(app_database_url)

    downgrade_to(app_database_url, "0701")
    try:
        assert probe.current_revision(app_database_url) == "0701"
        assert probe.row_security(app_database_url, INCIDENTS) is None
        assert probe.row_security(app_database_url, SOURCES) is None
    finally:
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)
