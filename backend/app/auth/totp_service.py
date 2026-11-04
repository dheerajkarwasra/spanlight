"""Two-factor authentication for one user: set up, enable, check a code, disable.

The arithmetic is in `app.auth.totp`; this module decides what may happen to the database rows.
Callers commit. Two rules matter more than the rest:

* **A code is spent once.** `verify` reads the user `FOR UPDATE`, so two sign-ins presenting one
  code are serialised: the first moves `totp_last_step` forward, the second finds the code's step
  no longer ahead of it and is refused. Without the lock both would read the old step and both
  would be let in.
* **A secret that cannot be read fails closed.** If the seed cannot be opened (the keyring lost
  its key, `CREDENTIALS_KEYS` was removed), `verify` raises instead of returning False or True.
  Treating it as "no second factor" would let anyone with the password past it; the caller turns
  the error into a response. Recovery codes need no key and still work.

Nothing here logs a secret, a code or a recovery code, and none is ever put in an exception
message.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.totp import (
    LEGACY_RECOVERY_CODE_LENGTH,
    hash_recovery_code,
    is_recovery_code,
    is_totp_code,
    match_step,
    new_recovery_codes,
    new_secret,
    normalize_code,
    provisioning_url,
)
from app.config import Settings
from app.core.crypto import Sealed, decrypt, encrypt
from app.db.models import AuditAction, RecoveryCode, User
from app.services.audit import record_user_audit_in_each_org


class TotpAlreadyEnabledError(Exception):
    """Two-factor authentication is already on for this user."""


class TotpNotEnabledError(Exception):
    """Two-factor authentication is not on for this user."""


class InvalidTotpCodeError(Exception):
    """The code is wrong, stale, already used, or there is nothing it could be right for."""


class NoPendingSetupError(InvalidTotpCodeError):
    """Enabling was attempted before a setup produced a secret to check the code against."""


@dataclass(frozen=True)
class RecoveryCodeStatus:
    remaining: int
    legacy: bool


@dataclass(frozen=True)
class SetupInfo:
    """What the user needs to add the account to an authenticator app. Shown once."""

    secret: str
    otpauth_url: str


async def lock_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """The user, read `FOR UPDATE` and refreshed, so the TOTP columns are never stale.

    The ORM object may already be in the session (the request's authentication loaded it);
    `populate_existing` makes this read overwrite its attributes with the locked row's.
    """
    return await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def begin_setup(db: AsyncSession, settings: Settings, user: User) -> SetupInfo:
    """Create a secret for the user and keep it, sealed, until a code confirms it.

    Replaces a secret from an earlier setup that was never confirmed. Raises
    `TotpAlreadyEnabledError`, and `CryptoNotConfigured` without `CREDENTIALS_KEYS`.
    """
    locked = await _locked(db, user)
    if locked.totp_enabled:
        raise TotpAlreadyEnabledError
    secret = new_secret()
    sealed = encrypt(secret.encode(), settings=settings)
    locked.totp_secret = sealed.ciphertext
    locked.totp_key_id = sealed.key_id
    locked.totp_last_step = None
    await db.flush()
    return SetupInfo(secret=secret, otpauth_url=provisioning_url(secret, locked.email))


async def enable(
    db: AsyncSession,
    settings: Settings,
    user: User,
    code: str,
    *,
    now: datetime,
    ip: str | None = None,
) -> list[str]:
    """Turn two-factor authentication on once `code` proves the authenticator app has the secret.

    Returns the recovery codes, in the form to show the user. This is the only moment they exist
    in clear text: the database keeps their hashes. Raises `TotpAlreadyEnabledError`,
    `NoPendingSetupError` and `InvalidTotpCodeError`.
    """
    locked = await _locked(db, user)
    if locked.totp_enabled:
        raise TotpAlreadyEnabledError
    if locked.totp_secret is None:
        raise NoPendingSetupError
    # A recovery code is not accepted here: none exists yet, and the point is to prove the app.
    step = match_step(_open_secret(settings, locked), code, now, None)
    if step is None:
        raise InvalidTotpCodeError

    codes = new_recovery_codes()
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == locked.id))
    db.add_all(
        RecoveryCode(
            user_id=locked.id,
            code_hash=hash_recovery_code(c),
            code_length=LEGACY_RECOVERY_CODE_LENGTH,
        )
        for c in codes
    )
    locked.totp_enabled_at = now
    # The code that proved the app is spent, like any other.
    locked.totp_last_step = step
    await db.flush()
    await record_user_audit_in_each_org(
        db, user_id=locked.id, action=AuditAction.USER_TOTP_ENABLE, ip=ip
    )
    return codes


async def verify(
    db: AsyncSession, settings: Settings, user: User, code: str, *, now: datetime
) -> bool:
    """Whether `code` is a valid second factor for the user right now; spends it if so.

    A six-digit code must be the TOTP code of a step in the window and later than the last one
    used. A 10- or 28-character code must be an unused recovery code. Anything else is False,
    as is a user without two-factor authentication. The user row stays locked until the caller
    ends the transaction, which is what serialises two attempts with the same code.
    """
    locked = await _locked(db, user)
    if not locked.totp_enabled:
        return False
    normalized = normalize_code(code)
    if is_totp_code(normalized):
        step = match_step(_open_secret(settings, locked), normalized, now, locked.totp_last_step)
        if step is None:
            return False
        locked.totp_last_step = step
        await db.flush()
        return True
    if is_recovery_code(normalized):
        return await _spend_recovery_code(db, locked.id, normalized, now)
    return False


async def disable(
    db: AsyncSession,
    settings: Settings,
    user: User,
    code: str,
    *,
    now: datetime,
    ip: str | None = None,
) -> None:
    """Turn two-factor authentication off, given a valid TOTP or recovery code.

    Deletes the secret and every recovery code, so enabling again starts from nothing. Raises
    `TotpNotEnabledError` and `InvalidTotpCodeError`.
    """
    locked = await _locked(db, user)
    if not locked.totp_enabled:
        raise TotpNotEnabledError
    if not await verify(db, settings, locked, code, now=now):
        raise InvalidTotpCodeError

    await _clear(db, locked)
    await record_user_audit_in_each_org(
        db, user_id=locked.id, action=AuditAction.USER_TOTP_DISABLE, ip=ip
    )


async def reset(db: AsyncSession, user: User) -> bool:
    """Turn two-factor authentication off without a code. For an operator, never a user's request.

    The way back in for someone who lost both the authenticator app and the recovery codes. It
    clears the same state as `disable` and returns whether two-factor authentication was on. The
    caller audits it (an operator is not the user) and commits.
    """
    locked = await _locked(db, user)
    was_enabled = locked.totp_enabled
    if was_enabled or locked.totp_secret is not None:
        # A setup that no code ever confirmed goes too, but there was no second factor to turn off.
        await _clear(db, locked)
    return was_enabled


async def _clear(db: AsyncSession, locked: User) -> None:
    """Forget the secret, the step and every recovery code, in one flush."""
    locked.totp_secret = None
    locked.totp_key_id = None
    locked.totp_enabled_at = None
    locked.totp_last_step = None
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == locked.id))
    await db.flush()


async def recovery_code_status(db: AsyncSession, user_id: uuid.UUID) -> RecoveryCodeStatus:
    """How many unused recovery codes the user has, and whether any is in the legacy format."""
    legacy = or_(
        RecoveryCode.code_length.is_(None),
        RecoveryCode.code_length == LEGACY_RECOVERY_CODE_LENGTH,
    )
    remaining, legacy_count = (
        await db.execute(
            select(func.count(), func.count().filter(legacy))
            .select_from(RecoveryCode)
            .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        )
    ).one()
    return RecoveryCodeStatus(remaining=int(remaining), legacy=legacy_count > 0)


async def _locked(db: AsyncSession, user: User) -> User:
    locked = await lock_user(db, user.id)
    if locked is None:
        # The caller holds a user it just loaded; it vanishing mid-request is not a case to
        # answer politely.
        raise LookupError("the user no longer exists")
    return locked


def _open_secret(settings: Settings, user: User) -> str:
    if user.totp_secret is None or user.totp_key_id is None:
        raise InvalidTotpCodeError
    sealed = Sealed(ciphertext=user.totp_secret, key_id=user.totp_key_id)
    return decrypt(sealed, settings=settings).decode()


async def _spend_recovery_code(
    db: AsyncSession, user_id: uuid.UUID, normalized: str, now: datetime
) -> bool:
    """Mark the code used if it exists and is unused. One statement, so it is atomic."""
    spent = await db.scalar(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == hash_recovery_code(normalized),
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=now)
        .returning(RecoveryCode.code_hash)
    )
    return spent is not None
