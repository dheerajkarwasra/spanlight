"""The TOTP service against a real database: enrolment, verification, replay and recovery codes."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import totp, totp_service
from app.auth.totp_service import (
    InvalidTotpCodeError,
    NoPendingSetupError,
    TotpAlreadyEnabledError,
    TotpNotEnabledError,
)
from app.config import Settings
from app.core.crypto import CryptoNotConfigured, Sealed, UnknownKeyId, decrypt
from app.db.models import RecoveryCode, User
from tests.auth.conftest import CREDENTIALS_KEYS

SessionFactory = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 10, 8, 12, 0, 10, tzinfo=UTC)
STEP = totp.step_at(NOW)


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    return settings.model_copy(update={"credentials_keys": SecretStr(CREDENTIALS_KEYS)})


@pytest.fixture
async def user_id(session_factory: SessionFactory) -> uuid.UUID:
    async with session_factory() as db:
        user = User(email="ada@example.com", password_hash=None, name="Ada")
        db.add(user)
        await db.commit()
        return user.id


async def load(db: AsyncSession, user_id: uuid.UUID) -> User:
    return (await db.scalars(select(User).where(User.id == user_id))).one()


async def enrolled(
    session_factory: SessionFactory, settings: Settings, user_id: uuid.UUID
) -> tuple[str, list[str]]:
    """Set up and enable at NOW; returns the secret and the recovery codes."""
    async with session_factory() as db:
        user = await load(db, user_id)
        info = await totp_service.begin_setup(db, settings, user)
        codes = await totp_service.enable(
            db, settings, user, totp.code_for_step(info.secret, STEP), now=NOW
        )
        await db.commit()
    return info.secret, codes


# --- setup -------------------------------------------------------------------------------------


async def test_setup_seals_the_secret_and_leaves_two_factor_off(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        info = await totp_service.begin_setup(db, keyed, user)
        await db.commit()

    async with session_factory() as db:
        stored = await load(db, user_id)
        assert stored.totp_enabled_at is None
        assert stored.totp_last_step is None
        assert stored.totp_secret is not None
        assert info.secret.encode() not in stored.totp_secret
        assert stored.totp_key_id == "test-key"
        opened = decrypt(Sealed(stored.totp_secret, stored.totp_key_id), settings=keyed)
        assert opened.decode() == info.secret
    assert info.otpauth_url.startswith("otpauth://totp/Spanlight:ada%40example.com?")


async def test_setup_again_replaces_the_pending_secret(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        first = await totp_service.begin_setup(db, keyed, user)
        second = await totp_service.begin_setup(db, keyed, user)
        await db.commit()

        assert first.secret != second.secret
        # The first secret is gone: its code no longer enables anything.
        with pytest.raises(InvalidTotpCodeError):
            await totp_service.enable(
                db, keyed, user, totp.code_for_step(first.secret, STEP), now=NOW
            )


async def test_setup_without_credentials_keys_is_refused_and_stores_nothing(
    session_factory: SessionFactory, settings: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(CryptoNotConfigured):
            await totp_service.begin_setup(db, settings, user)
        await db.rollback()

    async with session_factory() as db:
        assert (await load(db, user_id)).totp_secret is None


async def test_setup_when_enabled_is_refused_and_keeps_the_secret(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(TotpAlreadyEnabledError):
            await totp_service.begin_setup(db, keyed, user)
        assert await totp_service.verify(
            db, keyed, user, totp.code_for_step(secret, STEP + 1), now=NOW
        )


# --- enable ------------------------------------------------------------------------------------


async def test_enable_turns_it_on_and_returns_ten_recovery_codes_once(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)

    assert len(codes) == 10
    assert len(set(codes)) == 10
    async with session_factory() as db:
        stored = await load(db, user_id)
        assert stored.totp_enabled_at == NOW
        assert stored.totp_last_step == STEP
        rows = (await db.scalars(select(RecoveryCode).order_by(RecoveryCode.code_hash))).all()
        assert sorted(row.code_hash for row in rows) == sorted(
            totp.hash_recovery_code(code) for code in codes
        )
        assert all(row.used_at is None for row in rows)
        assert (await totp_service.recovery_code_status(db, stored.id)).remaining == 10


async def test_enable_with_a_wrong_code_changes_nothing(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        await totp_service.begin_setup(db, keyed, user)
        await db.commit()

        with pytest.raises(InvalidTotpCodeError):
            await totp_service.enable(db, keyed, user, "000000", now=NOW)
        await db.rollback()

    async with session_factory() as db:
        stored = await load(db, user_id)
        assert stored.totp_enabled_at is None
        assert (await db.scalar(select(func.count()).select_from(RecoveryCode))) == 0


async def test_enable_without_setup_is_refused(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(NoPendingSetupError):
            await totp_service.enable(db, keyed, user, "123456", now=NOW)


async def test_enable_when_already_enabled_is_refused(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(TotpAlreadyEnabledError):
            await totp_service.enable(
                db, keyed, user, totp.code_for_step(secret, STEP + 1), now=NOW
            )


async def test_enable_does_not_take_a_recovery_code(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        await totp_service.begin_setup(db, keyed, user)
        with pytest.raises(InvalidTotpCodeError):
            await totp_service.enable(db, keyed, user, "abcde-fghij", now=NOW)


# --- verify ------------------------------------------------------------------------------------


async def test_a_code_after_the_enrolment_step_verifies_and_moves_the_step_forward(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    later = NOW + timedelta(seconds=30)

    async with session_factory() as db:
        user = await load(db, user_id)
        ok = await totp_service.verify(
            db, keyed, user, totp.code_for_step(secret, STEP + 1), now=later
        )
        await db.commit()

    assert ok
    async with session_factory() as db:
        assert (await load(db, user_id)).totp_last_step == STEP + 1


async def test_a_used_code_is_refused_the_second_time(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    code = totp.code_for_step(secret, STEP + 1)
    later = NOW + timedelta(seconds=30)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert await totp_service.verify(db, keyed, user, code, now=later)
        await db.commit()

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(db, keyed, user, code, now=later)


async def test_the_code_used_to_enable_cannot_be_used_to_sign_in(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(
            db, keyed, user, totp.code_for_step(secret, STEP), now=NOW
        )


async def test_an_earlier_code_is_refused_after_a_later_one_was_used(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    later = NOW + timedelta(seconds=30)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert await totp_service.verify(
            db, keyed, user, totp.code_for_step(secret, STEP + 2), now=later
        )
        await db.commit()

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(
            db, keyed, user, totp.code_for_step(secret, STEP + 1), now=later
        )


async def test_a_wrong_code_does_not_move_the_step(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    later = NOW + timedelta(seconds=30)
    right = totp.code_for_step(secret, STEP + 1)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(db, keyed, user, wrong, now=later)
        await db.commit()

    async with session_factory() as db:
        assert (await load(db, user_id)).totp_last_step == STEP


async def test_verify_is_false_when_two_factor_is_off(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(db, keyed, user, "123456", now=NOW)
        # A pending (not yet enabled) secret does not count either.
        info = await totp_service.begin_setup(db, keyed, user)
        assert not await totp_service.verify(
            db, keyed, user, totp.code_for_step(info.secret, STEP), now=NOW
        )


async def test_two_verifies_of_one_code_cannot_both_win(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    code = totp.code_for_step(secret, STEP + 1)
    later = NOW + timedelta(seconds=30)

    async with session_factory() as first, session_factory() as second:
        first_user = await load(first, user_id)
        second_user = await load(second, user_id)
        assert await totp_service.verify(first, keyed, first_user, code, now=later)

        # The first transaction is still open and holds the user's row. The second verifier has to
        # wait for it; without the lock both would read the old step and both would win.
        waiting = asyncio.create_task(
            totp_service.verify(second, keyed, second_user, code, now=later)
        )
        finished, _ = await asyncio.wait({waiting}, timeout=0.5)
        assert not finished, "the second verify did not wait for the first"

        await first.commit()
        assert await asyncio.wait_for(waiting, timeout=5) is False


async def test_verify_never_passes_when_the_secret_cannot_be_opened(
    session_factory: SessionFactory, keyed: Settings, settings: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)
    code = totp.code_for_step(secret, STEP + 1)
    rotated = settings.model_copy(
        update={"credentials_keys": SecretStr("other:" + CREDENTIALS_KEYS.split(":", 1)[1])}
    )

    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(CryptoNotConfigured):
            await totp_service.verify(db, settings, user, code, now=NOW)
        with pytest.raises(UnknownKeyId):
            await totp_service.verify(db, rotated, user, code, now=NOW)


# --- recovery codes ----------------------------------------------------------------------------


async def test_a_recovery_code_signs_in_once(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert await totp_service.verify(db, keyed, user, codes[0], now=NOW)
        await db.commit()

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(db, keyed, user, codes[0], now=NOW)
        assert (await totp_service.recovery_code_status(db, user.id)).remaining == 9
        used = (
            await db.scalars(
                select(RecoveryCode).where(
                    RecoveryCode.code_hash == totp.hash_recovery_code(codes[0])
                )
            )
        ).one()
        assert used.used_at is not None
        # A recovery code is not a TOTP code: it leaves the step alone.
        assert (await load(db, user_id)).totp_last_step == STEP


async def test_a_recovery_code_is_accepted_however_it_is_typed(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert await totp_service.verify(db, keyed, user, codes[0].upper(), now=NOW)
        assert await totp_service.verify(db, keyed, user, codes[1].replace("-", " "), now=NOW)
        assert await totp_service.verify(db, keyed, user, f"  {codes[2]}\n", now=NOW)


async def test_a_code_that_was_never_issued_is_refused(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        assert not await totp_service.verify(db, keyed, user, "abcde-fghij", now=NOW)
        assert (await totp_service.recovery_code_status(db, user.id)).remaining == 10


async def test_one_users_recovery_code_does_not_work_for_another(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)
    async with session_factory() as db:
        other = User(email="bob@example.com", password_hash=None, name="Bob")
        db.add(other)
        await db.commit()
        info = await totp_service.begin_setup(db, keyed, other)
        await totp_service.enable(db, keyed, other, totp.code_for_step(info.secret, STEP), now=NOW)
        await db.commit()

        assert not await totp_service.verify(db, keyed, other, codes[0], now=NOW)


async def test_two_uses_of_one_recovery_code_cannot_both_win(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)

    async def use() -> bool:
        async with session_factory() as db:
            user = await load(db, user_id)
            ok = await totp_service.verify(db, keyed, user, codes[0], now=NOW)
            await db.commit()
            return ok

    results = await asyncio.gather(use(), use(), use())

    assert sorted(results) == [False, False, True]


# --- disable -----------------------------------------------------------------------------------


async def test_disable_with_a_totp_code_clears_everything(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    secret, _ = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        await totp_service.disable(
            db, keyed, user, totp.code_for_step(secret, STEP + 1), now=NOW + timedelta(seconds=30)
        )
        await db.commit()

    async with session_factory() as db:
        stored = await load(db, user_id)
        assert stored.totp_secret is None
        assert stored.totp_key_id is None
        assert stored.totp_enabled_at is None
        assert stored.totp_last_step is None
        assert (await db.scalar(select(func.count()).select_from(RecoveryCode))) == 0


async def test_disable_with_a_recovery_code_works_too(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    _, codes = await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        await totp_service.disable(db, keyed, user, codes[3], now=NOW)
        await db.commit()

    async with session_factory() as db:
        assert (await load(db, user_id)).totp_enabled_at is None


async def test_disable_with_a_wrong_code_changes_nothing(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    await enrolled(session_factory, keyed, user_id)

    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(InvalidTotpCodeError):
            await totp_service.disable(db, keyed, user, "000000", now=NOW)
        await db.rollback()

    async with session_factory() as db:
        assert (await load(db, user_id)).totp_enabled_at == NOW
        assert (await db.scalar(select(func.count()).select_from(RecoveryCode))) == 10


async def test_disable_when_off_is_refused(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        user = await load(db, user_id)
        with pytest.raises(TotpNotEnabledError):
            await totp_service.disable(db, keyed, user, "123456", now=NOW)


async def test_enrolling_again_after_disabling_starts_clean(
    session_factory: SessionFactory, keyed: Settings, user_id: uuid.UUID
) -> None:
    first_secret, first_codes = await enrolled(session_factory, keyed, user_id)
    async with session_factory() as db:
        user = await load(db, user_id)
        await totp_service.disable(db, keyed, user, first_codes[0], now=NOW)
        await db.commit()

    second_secret, second_codes = await enrolled(session_factory, keyed, user_id)

    assert second_secret != first_secret
    assert not set(second_codes) & set(first_codes)
    async with session_factory() as db:
        user = await load(db, user_id)
        assert (await totp_service.recovery_code_status(db, user.id)).remaining == 10
        assert not await totp_service.verify(db, keyed, user, first_codes[1], now=NOW)
