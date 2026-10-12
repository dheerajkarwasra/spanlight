"""The `oauth_identities` table: its constraints, and `users.password_hash` being optional."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, InternalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.db.models import OAuthIdentity, User

SessionFactory = async_sessionmaker[AsyncSession]


async def _user(db: AsyncSession, email: str, *, password_hash: str | None = None) -> User:
    user = User(email=email, password_hash=password_hash, name="Test")
    db.add(user)
    await db.flush()
    return user


async def test_a_user_may_have_no_password(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = await _user(db, "ada@example.com")
        await db.commit()
        assert user.password_hash is None
        assert user.has_password is False


async def test_the_provider_must_be_github_or_google(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = await _user(db, "ada@example.com")
        db.add(OAuthIdentity(user_id=user.id, provider="gitlab", subject="1"))
        with pytest.raises(IntegrityError, match="oauth_identities_provider_check"):
            await db.flush()


async def test_a_provider_account_belongs_to_one_user(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        grace = await _user(db, "grace@example.com")
        db.add(OAuthIdentity(user_id=ada.id, provider="github", subject="1"))
        await db.flush()
        db.add(OAuthIdentity(user_id=grace.id, provider="github", subject="1"))
        with pytest.raises(IntegrityError, match="oauth_identities_provider_subject_key"):
            await db.flush()


async def test_a_user_has_one_account_per_provider(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        db.add(OAuthIdentity(user_id=ada.id, provider="github", subject="1"))
        await db.flush()
        db.add(OAuthIdentity(user_id=ada.id, provider="github", subject="2"))
        with pytest.raises(IntegrityError, match="oauth_identities_user_id_provider_key"):
            await db.flush()


async def test_a_user_can_link_both_providers(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        db.add_all(
            [
                OAuthIdentity(user_id=ada.id, provider="github", subject="1"),
                OAuthIdentity(user_id=ada.id, provider="google", subject="1"),
            ]
        )
        await db.flush()


async def test_deleting_the_user_deletes_their_identities(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        db.add(OAuthIdentity(user_id=ada.id, provider="github", subject="1"))
        await db.commit()
        await db.execute(text("DELETE FROM users"))
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM oauth_identities")) == 0


async def test_a_new_identity_starts_with_its_timestamps_set(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        identity = OAuthIdentity(user_id=ada.id, provider="github", subject="1")
        db.add(identity)
        await db.commit()
        await db.refresh(identity)
        assert identity.created_at is not None
        assert identity.last_used_at is not None
        assert identity.email is None


def test_downgrade_refuses_to_strand_users_without_a_password(
    app_database_url: str,
) -> None:
    """Going back would need a password for every user; it fails, and changes nothing.

    Later migrations build indexes `CONCURRENTLY`, which commits mid-downgrade, so a single
    downgrade from head cannot be atomic. The database is first stepped down to 0005, the
    revision that adds the table; the refused step from there must leave it exactly as it was.
    """
    engine = create_engine(app_database_url)
    try:
        downgrade_to(app_database_url, "0005")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, name) "
                    "VALUES (gen_random_uuid(), 'oauth-only@example.com', NULL, 'No Password')"
                )
            )
        try:
            with pytest.raises((InternalError, DBAPIError), match="no password"):
                downgrade_to(app_database_url, "0004")
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                table = connection.execute(text("SELECT to_regclass('oauth_identities')")).scalar()
                stranded = connection.execute(
                    text("SELECT count(*) FROM users WHERE email = 'oauth-only@example.com'")
                ).scalar()
            assert revision == "0005"
            assert table == "oauth_identities"
            assert stranded == 1
        finally:
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM users WHERE email = 'oauth-only@example.com'"))
    finally:
        upgrade_to_head(app_database_url)
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        engine.dispose()
    assert revision == head_revision()


def test_downgrade_drops_the_oauth_audit_events_and_the_actions_they_used(
    app_database_url: str,
) -> None:
    """The older schema cannot hold `user.oauth_*` events, so they go and the check shrinks."""
    insert_event = text(
        "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), :org, :action, 'user', 'x')"
    )
    engine = create_engine(app_database_url)
    try:
        with engine.begin() as connection:
            org = connection.execute(
                text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (gen_random_uuid(), 'Acme', 'acme-downgrade') RETURNING id"
                )
            ).scalar()
            for action in ("user.oauth_link", "user.oauth_unlink", "member.add"):
                connection.execute(insert_event, {"org": org, "action": action})
        downgrade_to(app_database_url, "0004")
        try:
            with engine.connect() as connection:
                kept = connection.execute(text("SELECT action FROM audit_events")).scalars().all()
            assert list(kept) == ["member.add"]
            with (
                pytest.raises(IntegrityError, match="audit_events_action_check"),
                engine.begin() as connection,
            ):
                connection.execute(insert_event, {"org": org, "action": "user.oauth_link"})
        finally:
            upgrade_to_head(app_database_url)
        with engine.begin() as connection:
            connection.execute(insert_event, {"org": org, "action": "user.oauth_link"})
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM organizations WHERE slug = 'acme-downgrade'"))
        engine.dispose()


async def test_the_constraint_names_the_sign_in_code_relies_on_exist(
    session_factory: SessionFactory,
) -> None:
    """`oauth_service` tells a lost race from other errors by these names; a rename breaks it."""
    async with session_factory() as db:
        names = set(
            (
                await db.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE connamespace = 'public'::regnamespace"
                    )
                )
            ).all()
        )
    assert {
        "users_email_key",
        "oauth_identities_provider_subject_key",
        "oauth_identities_user_id_provider_key",
    } <= names
