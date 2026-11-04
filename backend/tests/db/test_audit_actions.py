"""Every audit action the application can write is accepted by the database."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditAction, AuditEvent, Organization, User
from tests.db.migration_support import downgrade_to, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.parametrize("action", list(AuditAction))
async def test_the_database_accepts_every_audit_action(
    session_factory: SessionFactory, action: AuditAction
) -> None:
    # `audit_events.action` is a CHECK over a fixed list. Adding a member to `AuditAction`
    # without extending that list in a migration fails here, not on the first real event.
    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash=None, name="Ada")
        org = Organization(name="Acme", slug="acme")
        db.add_all([user, org])
        await db.flush()
        db.add(
            AuditEvent(
                org_id=org.id,
                actor_user_id=user.id,
                action=action.value,
                target_type="user",
                target_id=str(user.id),
            )
        )
        await db.flush()


async def test_the_database_rejects_an_unknown_audit_action(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        org = Organization(name="Acme", slug="acme")
        db.add(org)
        await db.flush()
        db.add(AuditEvent(org_id=org.id, action="user.teleport", target_type="user", target_id="x"))
        with pytest.raises(IntegrityError, match="audit_events_action_check"):
            await db.flush()


@pytest.mark.migration_revision("0009")
def test_downgrading_past_project_delete_removes_its_audit_events_and_upgrading_restores_it(
    migration_database_url: str,
) -> None:
    """Migration 0009 adds `project.delete`; going back drops the events the old CHECK rejects."""
    insert_event = text(
        "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), :org, :action, 'project', 'x')"
    )
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            org = connection.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (gen_random_uuid(), 'Acme', 'acme-0009-downgrade') RETURNING id"
                )
            ).scalar()
            for action in ("project.delete", "project.update", "org.update"):
                connection.execute(insert_event, {"org": org, "action": action})

        downgrade_to(migration_database_url, "0008")
        try:
            with engine.connect() as connection:
                kept = (
                    connection.execute(text("SELECT action FROM audit_events ORDER BY action"))
                    .scalars()
                    .all()
                )
            assert list(kept) == ["org.update", "project.update"]
            with (
                pytest.raises(IntegrityError, match="audit_events_action_check"),
                engine.begin() as connection,
            ):
                connection.execute(insert_event, {"org": org, "action": "project.delete"})
        finally:
            upgrade_to_target(migration_database_url)

        with engine.begin() as connection:
            connection.execute(insert_event, {"org": org, "action": "project.delete"})
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM organizations WHERE slug = 'acme-0009-downgrade'"))
        engine.dispose()
