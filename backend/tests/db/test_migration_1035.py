"""Migration 1035: the retention job's cursor rows, their constraints, grants and round trip."""

from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from app.db.roles import ensure_app_role
from tests.conftest import _libpq
from tests.db import schema_probe as probe
from tests.db.migration_support import (
    admin_database_url,
    downgrade_to,
    expected_revision,
    upgrade_to_target,
)

BELOW = "1034"
TABLE = "retention_progress"
SEQUENCE = "retention_attempt_order_seq"
# PUBLIC is grantee 0 in an ACL; neither object may carry one.
PUBLIC_GRANTS = (
    "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a WHERE c.relname = %s AND a.grantee = 0"
)


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


@pytest.mark.migration_revision("1035")
def test_migration_1035_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = _libpq(migration_database_url)
    with psycopg.connect(url) as db:
        assert db.execute(
            f"SELECT count(*), min(attempt_order), max(attempt_order), "
            f"count(after_scope_id) FROM {TABLE}"
        ).fetchone() == (14, 0, 0, 0)
        # The first opportunity orders after every seed.
        assert db.execute(f"SELECT nextval('{SEQUENCE}')").fetchone() == (1,)
        for statement in (
            f"INSERT INTO {TABLE} (class, attempt_order) VALUES ('sessions', 0)",
            f"UPDATE {TABLE} SET attempt_order = -1 WHERE class = 'scores'",
            f"UPDATE {TABLE} SET after_scope_id = gen_random_uuid() WHERE class = 'audit_events'",
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                db.execute(statement)
            db.rollback()
        assert db.execute(
            f"SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = '{TABLE}'"
        ).fetchone() == (False, False)
        for relation in (TABLE, SEQUENCE):
            assert db.execute(PUBLIC_GRANTS, (relation,)).fetchone() == (0,), relation

    downgrade_to(migration_database_url, BELOW)
    with psycopg.connect(url) as db:
        assert db.execute(
            f"SELECT to_regclass('{TABLE}'), to_regclass('{SEQUENCE}')"
        ).fetchone() == (
            None,
            None,
        )

    upgrade_to_target(migration_database_url)
    with psycopg.connect(url) as db:
        assert db.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (14,)


@pytest.mark.migration_revision("1035")
def test_ordinary_app_role_rotates_cursors_after_provisioning(migration_database_url: str) -> None:
    role = "retention_" + uuid4().hex[:12]
    ensure_app_role(_libpq(admin_database_url()), role, "isolated-test")
    parts = urlsplit(_libpq(migration_database_url))
    url = urlunsplit(parts._replace(netloc=f"{role}:isolated-test@{parts.hostname}:{parts.port}"))
    with psycopg.connect(url) as db:
        assert db.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone() == (False, False)
        assert db.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (14,)
        rotated = db.execute(
            f"UPDATE {TABLE} SET attempt_order = nextval('{SEQUENCE}'), "
            "after_scope_id = gen_random_uuid(), last_attempted_at = clock_timestamp() "
            "WHERE class = 'trace_cascades' RETURNING attempt_order"
        ).fetchone()
        assert rotated is not None and rotated[0] >= 1
        assert db.execute(
            f"SELECT has_sequence_privilege(current_user, '{SEQUENCE}', 'USAGE'), "
            f"has_sequence_privilege(current_user, '{SEQUENCE}', 'UPDATE')"
        ).fetchone() == (True, False)
        db.rollback()
