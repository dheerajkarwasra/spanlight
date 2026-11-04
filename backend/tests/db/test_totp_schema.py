"""The TOTP columns, `recovery_codes` and `organizations.require_2fa`, and migration 0006."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Organization, RecoveryCode, User
from tests.db.migration_support import downgrade_to, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
HASH = b"\x01" * 32


async def _user(db: AsyncSession, email: str = "ada@example.com", **columns: object) -> User:
    user = User(email=email, password_hash=None, name="Ada", **columns)
    db.add(user)
    await db.flush()
    return user


async def test_a_new_user_has_two_factor_off(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = await _user(db)

        assert user.totp_secret is None
        assert user.totp_key_id is None
        assert user.totp_enabled_at is None
        assert user.totp_last_step is None
        assert user.totp_enabled is False


async def test_a_user_can_hold_a_pending_secret_then_an_enabled_one(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        user = await _user(db, totp_secret=b"sealed", totp_key_id="k1")
        user.totp_enabled_at = NOW
        user.totp_last_step = 59_000_000
        await db.flush()

        assert user.totp_enabled is True


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        # A sealed secret and the key it was sealed under travel together.
        ({"totp_secret": b"sealed"}, "users_totp_secret_key_id_check"),
        ({"totp_key_id": "k1"}, "users_totp_secret_key_id_check"),
        # Enabled means there is a secret to check codes against.
        ({"totp_enabled_at": NOW}, "users_totp_enabled_check"),
        # A used step only exists while 2FA is on.
        (
            {"totp_secret": b"sealed", "totp_key_id": "k1", "totp_last_step": 5},
            "users_totp_last_step_check",
        ),
    ],
)
async def test_the_schema_refuses_half_configured_two_factor(
    session_factory: SessionFactory, columns: dict[str, object], constraint: str
) -> None:
    async with session_factory() as db:
        db.add(User(email="ada@example.com", password_hash=None, name="Ada", **columns))
        with pytest.raises(IntegrityError, match=constraint):
            await db.flush()


async def test_a_recovery_code_is_one_row_per_user_and_hash(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        ada = await _user(db, "ada@example.com")
        grace = await _user(db, "grace@example.com")
        db.add_all([RecoveryCode(user_id=ada.id, code_hash=HASH)])
        await db.flush()
        # The same hash for another user is a different row (and a different person's code).
        db.add(RecoveryCode(user_id=grace.id, code_hash=HASH))
        await db.flush()
        db.add(RecoveryCode(user_id=ada.id, code_hash=HASH))
        with pytest.raises(IntegrityError, match="recovery_codes_pkey"):
            await db.flush()


async def test_a_recovery_code_hash_is_a_sha256_digest(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        ada = await _user(db)
        db.add(RecoveryCode(user_id=ada.id, code_hash=b"too short"))
        with pytest.raises(IntegrityError, match="recovery_codes_code_hash_check"):
            await db.flush()


async def test_deleting_a_user_deletes_their_recovery_codes(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        ada = await _user(db)
        db.add(RecoveryCode(user_id=ada.id, code_hash=HASH))
        await db.commit()
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": ada.id})
        await db.commit()

        assert await db.scalar(select(func.count()).select_from(RecoveryCode)) == 0


async def test_an_org_does_not_require_two_factor_by_default(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        org = Organization(name="Acme", slug="acme")
        db.add(org)
        await db.flush()
        await db.refresh(org)

        assert org.require_2fa is False


async def test_require_2fa_cannot_be_null(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        with pytest.raises(IntegrityError, match="require_2fa"):
            await db.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, require_2fa) "
                    "VALUES (gen_random_uuid(), 'Acme', 'acme', NULL)"
                )
            )


@pytest.mark.migration_revision("0006")
def test_downgrade_removes_two_factor_and_its_audit_events_and_upgrade_restores_the_schema(
    migration_database_url: str,
) -> None:
    """Going back drops the data the older schema cannot hold; going forward starts clean."""
    insert_event = text(
        "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
        "VALUES (gen_random_uuid(), :org, :action, 'user', 'x')"
    )
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            org = connection.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, require_2fa) "
                    "VALUES (gen_random_uuid(), 'Acme', 'acme-totp-downgrade', true) RETURNING id"
                )
            ).scalar()
            user = connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, name, totp_secret, totp_key_id, "
                    "totp_enabled_at) VALUES (gen_random_uuid(), 'totp-downgrade@example.com', "
                    "'x', 'T', '\\x01', 'k1', now()) RETURNING id"
                )
            ).scalar()
            connection.execute(
                text("INSERT INTO recovery_codes (user_id, code_hash) VALUES (:u, :h)"),
                {"u": user, "h": HASH},
            )
            for action in ("user.totp_enable", "user.totp_disable", "org.update", "member.add"):
                connection.execute(insert_event, {"org": org, "action": action})

        downgrade_to(migration_database_url, "0005")
        try:
            with engine.connect() as connection:
                kept = connection.execute(text("SELECT action FROM audit_events")).scalars().all()
                table = connection.execute(text("SELECT to_regclass('recovery_codes')")).scalar()
                columns = (
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND "
                            "(column_name LIKE 'totp%' OR column_name = 'require_2fa')"
                        )
                    )
                    .scalars()
                    .all()
                )
            assert list(kept) == ["member.add"]
            assert table is None
            assert list(columns) == []
            with (
                pytest.raises(IntegrityError, match="audit_events_action_check"),
                engine.begin() as connection,
            ):
                connection.execute(insert_event, {"org": org, "action": "org.update"})
        finally:
            upgrade_to_target(migration_database_url)

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT totp_enabled_at, totp_secret FROM users WHERE id = :u"), {"u": user}
            ).one()
            required = connection.execute(
                text("SELECT require_2fa FROM organizations WHERE id = :o"), {"o": org}
            ).scalar()
        assert tuple(row) == (None, None)
        assert required is False
        with engine.begin() as connection:
            connection.execute(insert_event, {"org": org, "action": "org.update"})
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM organizations WHERE slug = 'acme-totp-downgrade'"))
            connection.execute(text("DELETE FROM users WHERE email = 'totp-downgrade@example.com'"))
        engine.dispose()
