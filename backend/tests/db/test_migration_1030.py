"""Migration 1030: terms acceptance on `users`, email-change tokens and the account audit actions.

The terms pair is one fact (a version and when it was accepted), so the database refuses half of
it. Historical round trips use an owned database at the pinned revision.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditAction, AuditEvent, Organization, User
from app.db.models.auth import EmailToken, EmailTokenKind
from app.services.audit import record_audit
from tests.db import schema_probe as probe
from tests.db.migration_support import downgrade_to, expected_revision, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]
BELOW = "1029"
PAIR_CHECK = "users_terms_pair_check"
NEW_ACTIONS = (
    AuditAction.USER_TERMS_ACCEPT,
    AuditAction.USER_UPDATE,
    AuditAction.USER_EMAIL_CHANGE,
    AuditAction.USER_DELETE,
)


async def test_users_terms_columns_must_be_set_together(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash=None, name="Ada")
        db.add(user)
        await db.flush()
        user.terms_version = "2026-11-01"
        user.terms_accepted_at = datetime.now(UTC)
        await db.flush()

        with pytest.raises(IntegrityError, match=PAIR_CHECK):
            await db.execute(
                text("UPDATE users SET terms_accepted_at = NULL WHERE id = :id"), {"id": user.id}
            )

    async with session_factory() as db:
        user = User(email="grace@example.com", password_hash=None, name="Grace")
        db.add(user)
        await db.flush()

        with pytest.raises(IntegrityError, match=PAIR_CHECK):
            await db.execute(
                text("UPDATE users SET terms_accepted_at = now() WHERE id = :id"), {"id": user.id}
            )


async def test_email_change_kind_and_new_email_exist(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash=None, name="Ada")
        db.add(user)
        await db.flush()
        db.add(
            EmailToken(
                user_id=user.id,
                kind=EmailTokenKind.EMAIL_CHANGE,
                token_hash=b"\x01" * 32,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                new_email="ADA@new.example.com",
            )
        )
        await db.flush()
        stored = await db.scalar(select(EmailToken.new_email).where(EmailToken.user_id == user.id))
        assert stored == "ADA@new.example.com"


async def test_new_audit_actions_are_allowed(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash=None, name="Ada")
        org = Organization(name="Acme", slug="acme")
        db.add_all([user, org])
        await db.flush()
        for action in NEW_ACTIONS:
            await record_audit(
                db,
                org_id=org.id,
                actor_user_id=user.id,
                action=action,
                target_type="user",
                target_id=user.id,
            )
        await db.flush()
        written = (await db.scalars(select(AuditEvent.action))).all()
        assert sorted(written) == sorted(action.value for action in NEW_ACTIONS)


@pytest.fixture
def back_to_target(migration_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_target(migration_database_url)
    assert probe.current_revision(migration_database_url) == expected_revision()


@pytest.mark.migration_revision("1030")
def test_migration_1030_round_trip(migration_database_url: str, back_to_target: None) -> None:
    url = migration_database_url
    users = probe.columns(url, "users")
    assert users["terms_version"][:2] == ("text", "YES")
    assert users["terms_accepted_at"][:2] == ("timestamptz", "YES")
    assert probe.columns(url, "email_tokens")["new_email"][:2] == ("citext", "YES")
    assert probe.constraints(url, "users")[PAIR_CHECK][1] is True
    action_check = probe.constraints(url, "audit_events")["audit_events_action_check"]
    assert "user.delete" in action_check[0] and action_check[1] is True

    downgrade_to(url, BELOW)
    assert "terms_version" not in probe.columns(url, "users")
    assert "new_email" not in probe.columns(url, "email_tokens")
    assert PAIR_CHECK not in probe.constraints(url, "users")
    action_check = probe.constraints(url, "audit_events")["audit_events_action_check"]
    assert "user.delete" not in action_check[0] and action_check[1] is False
    # Postgres cannot drop an enum value, so the downgrade leaves it.
    kinds = probe.query(url, "SELECT unnest(enum_range(NULL::email_token_kind))::text")
    assert ("email_change",) in kinds

    upgrade_to_target(url)
    assert probe.constraints(url, "users")[PAIR_CHECK][1] is True
