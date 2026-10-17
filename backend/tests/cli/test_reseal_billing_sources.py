"""`spanlight reseal-credentials` also reseals the admin keys of billing sources.

A master key may leave CREDENTIALS_KEYS only once nothing is sealed under it, so the command walks
`billing_sources` as well as `provider_credentials`, counts both, and prints counts only: never a
key. A CSV source holds no key and is skipped.
"""

import base64
from collections.abc import Iterator

import psycopg
import pytest
from typer.testing import CliRunner

from app import cli
from app.config import Settings, get_settings
from app.core.crypto import Sealed, decrypt, encrypt

V1 = "v1:" + base64.b64encode(bytes(range(32))).decode()
V2 = "v2:" + base64.b64encode(bytes(range(32, 64))).decode()
ORG = "00000000-0000-7000-8000-000000000001"
API_SOURCE = "00000000-0000-7000-8000-0000000000a1"
CSV_SOURCE = "00000000-0000-7000-8000-0000000000a2"
CREDENTIAL = "00000000-0000-7000-8000-0000000000c1"
ADMIN_KEY = b"sk-ant-admin01-reseal-test-key-0123456789"
PROVIDER_KEY = b"sk-ant-api03-reseal-test-key-0123456789"


def _settings(keys: str) -> Settings:
    return Settings(_env_file=None, credentials_keys=keys)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    monkeypatch.setenv("CREDENTIALS_KEYS", f"{V2},{V1}")
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _connect(app_database_url: str) -> psycopg.Connection:
    return psycopg.connect(app_database_url.replace("postgresql+psycopg://", "postgresql://"))


def _seed(app_database_url: str) -> None:
    """One API source and one credential sealed under `v1`, and one CSV source."""
    old = _settings(V1)
    admin, provider = encrypt(ADMIN_KEY, settings=old), encrypt(PROVIDER_KEY, settings=old)
    with _connect(app_database_url) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Acme', 'acme')", (ORG,)
        )
        conn.execute(
            "INSERT INTO billing_sources (id, org_id, kind, provider, name, ciphertext, key_id) "
            "VALUES (%s, %s, 'anthropic_admin_api', 'anthropic', 'Admin', %s, %s)",
            (API_SOURCE, ORG, admin.ciphertext, admin.key_id),
        )
        conn.execute(
            "INSERT INTO billing_sources (id, org_id, kind, provider, name) "
            "VALUES (%s, %s, 'csv_upload', 'openai', 'Exports')",
            (CSV_SOURCE, ORG),
        )
        conn.execute(
            "INSERT INTO provider_credentials (id, org_id, provider, name, ciphertext, key_id) "
            "VALUES (%s, %s, 'anthropic', 'Main', %s, %s)",
            (CREDENTIAL, ORG, provider.ciphertext, provider.key_id),
        )


def test_reseal_covers_billing_sources(runner: CliRunner, app_database_url: str) -> None:
    _seed(app_database_url)

    result = runner.invoke(cli.app, ["reseal-credentials"])

    assert result.exit_code == 0, result.output
    assert "Re-sealed 1 provider credentials and 1 billing source keys." in result.output
    assert ADMIN_KEY.decode() not in result.output
    assert PROVIDER_KEY.decode() not in result.output
    with _connect(app_database_url) as conn:
        sources = dict(
            conn.execute("SELECT id::text, key_id FROM billing_sources ORDER BY id").fetchall()
        )
        [(ciphertext, key_id)] = conn.execute(
            "SELECT ciphertext, key_id FROM billing_sources WHERE id = %s", (API_SOURCE,)
        ).fetchall()
        [(credential_key_id,)] = conn.execute("SELECT key_id FROM provider_credentials").fetchall()
    assert sources == {API_SOURCE: "v2", CSV_SOURCE: None}
    assert credential_key_id == "v2"
    # The new seal opens with `v2` alone: `v1` may now leave the keyring.
    assert decrypt(Sealed(ciphertext=ciphertext, key_id=key_id), settings=_settings(V2)) == (
        ADMIN_KEY
    )


def test_reseal_fails_while_a_billing_source_needs_a_removed_key(
    runner: CliRunner, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(app_database_url)
    # `v1` already left the keyring: the source cannot be opened, so the old key is still needed.
    monkeypatch.setenv("CREDENTIALS_KEYS", V2)
    get_settings.cache_clear()

    result = runner.invoke(cli.app, ["reseal-credentials"])

    assert result.exit_code == 1
    assert "Re-sealed 0 provider credentials and 0 billing source keys." in result.output
    assert "2 provider credentials or billing sources could not be opened" in result.output
    assert "2 provider credentials or billing sources are still sealed" in result.output
    assert ADMIN_KEY.decode() not in result.output
