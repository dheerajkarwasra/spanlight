"""Provider constraints and rollback against the invocation-owned PostgreSQL database."""

from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, upgrade_to_target

BELOW = "1050"
AZURE_URL = "https://example.openai.azure.com/openai/v1"
FIXED = ("gemini", "mistral", "groq", "deepseek", "xai", "together")


def _insert_credential(
    connection: psycopg.Connection, org_id: UUID, provider: str, base_url: str | None
) -> None:
    connection.execute(
        "INSERT INTO provider_credentials "
        "(id, org_id, provider, name, base_url, ciphertext, key_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'test')",
        (uuid4(), org_id, provider, str(uuid4()), base_url, b"test"),
    )


@pytest.mark.migration_revision("1051")
def test_provider_credentials_enforce_base_url_after_upgrade(migration_database_url: str) -> None:
    org_id = uuid4()
    downgrade_to(migration_database_url, BELOW)
    with probe.admin() as connection:
        probe.seed_project(connection, org_id, uuid4())
        _insert_credential(connection, org_id, "gemini", None)

    upgrade_to_target(migration_database_url)
    with probe.admin() as connection:
        assert connection.execute("SELECT provider FROM provider_credentials").fetchall() == [
            ("gemini",)
        ]
        for provider in FIXED:
            _insert_credential(connection, org_id, provider, None)
            with pytest.raises(psycopg.errors.CheckViolation, match="base_url_matches_provider"):
                _insert_credential(connection, org_id, provider, AZURE_URL)
        for provider in ("azure_openai", "openai_compatible"):
            _insert_credential(connection, org_id, provider, AZURE_URL)
            with pytest.raises(psycopg.errors.CheckViolation, match="base_url_matches_provider"):
                _insert_credential(connection, org_id, provider, None)


@pytest.mark.migration_revision("1051")
def test_expansion_accepts_csv_billing_and_preserves_api_bindings(
    migration_database_url: str,
) -> None:
    org_id = uuid4()
    with probe.admin() as connection:
        probe.seed_project(connection, org_id, uuid4())
        for provider in (
            "google",
            "azure_openai",
            "mistral",
            "groq",
            "deepseek",
            "xai",
            "together",
        ):
            connection.execute(
                "INSERT INTO billing_sources (id, org_id, kind, provider, name) "
                "VALUES (%s, %s, 'csv_upload', %s, %s)",
                (uuid4(), org_id, provider, provider),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="billing_sources_kind_provider"):
            connection.execute(
                "INSERT INTO billing_sources "
                "(id, org_id, kind, provider, name, ciphertext, key_id) "
                "VALUES (%s, %s, 'openai_costs_api', 'azure_openai', 'api', %s, 'test')",
                (uuid4(), org_id, b"test"),
            )


@pytest.mark.migration_revision("1051")
def test_new_status_providers_are_allowed_without_seeding_unverified_feeds(
    migration_database_url: str,
) -> None:
    assert probe.query(
        migration_database_url, "SELECT provider FROM provider_status_sources ORDER BY provider"
    ) == [("anthropic",), ("google",), ("openai",)]

    with probe.admin() as connection:
        for provider in ("mistral", "groq", "deepseek", "together"):
            connection.execute(
                "INSERT INTO provider_status_sources (provider, url) VALUES (%s, %s)",
                (provider, "https://example.com/incidents"),
            )
            connection.execute(
                "INSERT INTO provider_incidents "
                "(id, provider, source, external_id, title, impact, status, api_affected, "
                "started_at, url, fetched_at) "
                "VALUES (%s, %s, 'statuspage_json', 'one', 'API', 'minor', 'monitoring', true, "
                "now(), 'https://example.com/incidents/one', now())",
                (uuid4(), provider),
            )


@pytest.mark.migration_revision("1051")
def test_downgrade_preserves_new_provider_rows(migration_database_url: str) -> None:
    org_id = uuid4()
    downgrade_to(migration_database_url, BELOW)
    assert probe.current_revision(migration_database_url) == BELOW
    with probe.admin() as connection:
        probe.seed_project(connection, org_id, uuid4())
        with pytest.raises(psycopg.errors.CheckViolation, match="base_url_matches_provider"):
            _insert_credential(connection, org_id, "mistral", None)

    upgrade_to_target(migration_database_url)
    with probe.admin() as connection:
        _insert_credential(connection, org_id, "mistral", None)

    with pytest.raises(DBAPIError, match="new provider rows"):
        downgrade_to(migration_database_url, BELOW)

    assert probe.query(migration_database_url, "SELECT provider FROM provider_credentials") == [
        ("mistral",)
    ]


@pytest.mark.migration_revision("1052")
def test_feed_registration_downgrade_keeps_incident_history(migration_database_url: str) -> None:
    with probe.admin() as connection:
        connection.execute(
            "INSERT INTO provider_incidents "
            "(id, provider, source, external_id, title, impact, status, api_affected, "
            "started_at, url, fetched_at) "
            "VALUES (%s, 'mistral', 'statuspage_json', 'one', 'API', 'minor', 'monitoring', true, "
            "now(), 'https://status.mistral.ai/incidents/one', now())",
            (uuid4(),),
        )

    downgrade_to(migration_database_url, "1051")
    feeds = "SELECT provider FROM provider_status_sources ORDER BY provider"
    assert probe.query(migration_database_url, feeds) == [("anthropic",), ("google",), ("openai",)]
    assert probe.query(migration_database_url, "SELECT provider FROM provider_incidents") == [
        ("mistral",)
    ]

    upgrade_to_target(migration_database_url)
    assert probe.query(migration_database_url, feeds) == [
        ("anthropic",),
        ("google",),
        ("groq",),
        ("mistral",),
        ("openai",),
    ]
