"""Migration 1041: `recovery_codes.code_length` and the `user.totp_recovery_regenerate` action.

Rows stored before the column stay NULL (legacy, no backfill). Downgrade refuses while a
28-character code or a regenerate audit event exists, and otherwise restores 1040's schema.
Historical round trips use an owned database at the pinned revision.
"""

import hashlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

BELOW = "1040"
LENGTH_CHECK = "ck_recovery_codes_code_length"
ACTION_CHECK = "audit_events_action_check"
NEW_ACTION = "user.totp_recovery_regenerate"


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


def _execute(url: str, sql: str, **params: object) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(sql), params)
    finally:
        engine.dispose()


def _seed_user(url: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    _execute(
        url,
        "INSERT INTO users (id, email, name) VALUES (:id, :email, 'Ada')",
        id=user_id,
        email=f"{user_id}@example.com",
    )
    return user_id


def _add_code(url: str, user_id: uuid.UUID, seed: str, length: int | None = None) -> None:
    columns, values = "user_id, code_hash", ":user_id, :hash"
    if length is not None:
        columns, values = f"{columns}, code_length", f"{values}, :length"
    _execute(
        url,
        f"INSERT INTO recovery_codes ({columns}) VALUES ({values})",
        user_id=user_id,
        hash=hashlib.sha256(seed.encode()).digest(),
        length=length,
    )


@pytest.mark.migration_revision("1041")
def test_migration_1041_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    downgrade_to(url, BELOW)
    assert "code_length" not in probe.columns(url, "recovery_codes")
    user_id = _seed_user(url)
    _add_code(url, user_id, "old")

    upgrade_to_target(url)
    assert probe.columns(url, "recovery_codes")["code_length"][:2] == ("int2", "YES")
    assert probe.constraints(url, "recovery_codes")[LENGTH_CHECK][1] is True
    action_check = probe.constraints(url, "audit_events")[ACTION_CHECK]
    assert NEW_ACTION in action_check[0] and action_check[1] is True
    # The pre-existing row is kept as it was: NULL means legacy.
    assert probe.query(url, "SELECT code_length FROM recovery_codes") == [(None,)]
    _add_code(url, user_id, "ten", 10)
    with pytest.raises(IntegrityError, match=LENGTH_CHECK):
        _add_code(url, user_id, "eleven", 11)

    downgrade_to(url, BELOW)
    assert "code_length" not in probe.columns(url, "recovery_codes")
    assert LENGTH_CHECK not in probe.constraints(url, "recovery_codes")
    action_check = probe.constraints(url, "audit_events")[ACTION_CHECK]
    assert NEW_ACTION not in action_check[0] and "user.delete" in action_check[0]
    assert probe.query(url, "SELECT count(*) FROM recovery_codes") == [(2,)]


@pytest.mark.migration_revision("1041")
def test_migration_1041_downgrade_refuses_while_a_28_character_code_exists(
    migration_database_url: str, back_to_target: None
) -> None:
    url = migration_database_url
    _add_code(url, _seed_user(url), "new", 28)

    with pytest.raises(ProgrammingError, match="28-character recovery codes exist"):
        downgrade_to(url, BELOW)
    assert probe.current_revision(url) == "1041"
    assert "code_length" in probe.columns(url, "recovery_codes")


@pytest.mark.migration_revision("1041")
def test_migration_1041_downgrade_refuses_while_a_regenerate_event_exists(
    migration_database_url: str, back_to_target: None
) -> None:
    url = migration_database_url
    org_id = uuid.uuid4()
    _execute(
        url, "INSERT INTO organizations (id, name, slug) VALUES (:id, 'Acme', 'acme')", id=org_id
    )
    _execute(
        url,
        "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), :org, :action, 'user', 'x')",
        org=org_id,
        action=NEW_ACTION,
    )

    with pytest.raises(ProgrammingError, match="audit events exist"):
        downgrade_to(url, BELOW)
    assert probe.current_revision(url) == "1041"
    assert NEW_ACTION in probe.constraints(url, "audit_events")[ACTION_CHECK][0]
