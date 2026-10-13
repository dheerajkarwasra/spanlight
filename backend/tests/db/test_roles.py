"""`ensure_app_role` provisions the non-superuser role the app connects as."""

from collections.abc import Iterator

import psycopg
import pytest

from app.db.roles import ensure_app_role
from tests.conftest import admin_database_url

PROBE_ROLE = "spanlight_role_probe"


def _plain_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def admin_url(app_database_url: str) -> Iterator[str]:
    # app_database_url guarantees the schema is migrated before we grant on it.
    url = admin_database_url()
    yield url
    with psycopg.connect(_plain_url(url), autocommit=True) as connection:
        if connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (PROBE_ROLE,)
        ).fetchone():
            connection.execute(f"DROP OWNED BY {PROBE_ROLE}")
            connection.execute(f"DROP ROLE {PROBE_ROLE}")


def test_creates_a_role_that_cannot_bypass_row_level_security(admin_url: str) -> None:
    ensure_app_role(admin_url, PROBE_ROLE, "first-password-123")

    with psycopg.connect(_plain_url(admin_url)) as connection:
        row = connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            (PROBE_ROLE,),
        ).fetchone()
        can_write_spans = connection.execute(
            "SELECT has_table_privilege(%s, 'spans', 'SELECT, INSERT, UPDATE, DELETE')",
            (PROBE_ROLE,),
        ).fetchone()

    assert row == (False, False, True)
    assert can_write_spans == (True,)


def test_grants_the_functions_revoked_from_public(admin_url: str) -> None:
    ensure_app_role(admin_url, PROBE_ROLE, "first-password-123")

    with psycopg.connect(_plain_url(admin_url)) as connection:
        can_sum_spend = connection.execute(
            "SELECT has_function_privilege(%s, "
            "'explain_month_to_date_cost(uuid, timestamptz)', 'EXECUTE')",
            (PROBE_ROLE,),
        ).fetchone()

    assert can_sum_spend == (True,)


def test_is_idempotent_and_rotates_the_password(admin_url: str) -> None:
    ensure_app_role(admin_url, PROBE_ROLE, "first-password-123")
    ensure_app_role(admin_url, PROBE_ROLE, "second-password-456")

    probe_url = _plain_url(admin_url).replace(
        "postgres:postgres@", f"{PROBE_ROLE}:second-password-456@", 1
    )
    with psycopg.connect(probe_url) as connection:
        assert connection.execute("SELECT current_user").fetchone() == (PROBE_ROLE,)


def test_rejects_unsafe_role_names(admin_url: str) -> None:
    with pytest.raises(ValueError, match="Unsafe role name"):
        ensure_app_role(admin_url, "app; DROP TABLE spans", "first-password-123")
