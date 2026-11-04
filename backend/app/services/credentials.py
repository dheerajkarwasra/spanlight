"""Replacing a password, and signing in against it, without a session outliving the change.

Signing in and replacing a password can race. A sign-in verifies the old password (argon2 takes
tens of milliseconds), a reset commits meanwhile and deletes every session, and then the sign-in
inserts its own: a session that survives a password that no longer exists. A set-based DELETE
only removes the rows committed when it runs, so the DELETE alone cannot close that window.

The user row is where the two sides meet, and both take its lock in the same order:

* `replace_password` writes the new hash first (an UPDATE, which locks the row until commit) and
  only then deletes the sessions.
* A sign-in calls `lock_user_for_sign_in` after the slow verification and before creating its
  session. That reads the row `FOR SHARE`, which holds it until the sign-in commits.

So whichever gets the row first, the other has to wait for it. If the reset is first, the
sign-in waits for its commit, sees the new hash and is refused. If the sign-in is first, the
reset's UPDATE waits for the sign-in's commit, and the DELETE that follows sees (and removes)
the session it created. The slow hashing happens outside the lock on both sides, so the lock
is held for a few statements.

The same locked read also answers whether two-factor authentication is on, so a sign-in decides
whether to ask for the second step from the row it holds, not from the copy it read before the
slow verification. Turning it on is an UPDATE of that row, so it either committed before the lock
(and is seen) or waits for the sign-in to commit.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.email_tokens import retire_unused_tokens
from app.db.models import EmailTokenKind, User
from app.services.sessions import revoke_all_sessions


async def replace_password(db: AsyncSession, user: User, password_hash: str) -> None:
    """Set the user's password hash, end every session they have and cancel a pending email
    change. The caller commits.

    The hash is flushed before the sessions are deleted, not left to autoflush, because that
    order is what makes a concurrent sign-in either wait or get deleted (see the module
    docstring). Callers set anything else they want to change on `user` before calling this, so
    it all goes out in the same UPDATE.
    """
    user.password_hash = password_hash
    await db.flush()
    await revoke_all_sessions(db, user.id)
    # Someone who knew the old password may have asked to move the account to their address.
    await retire_unused_tokens(db, user.id, EmailTokenKind.EMAIL_CHANGE)


@dataclass(frozen=True)
class LockedUser:
    """What a sign-in needs from the user row, as it is now that the row is locked."""

    password_hash: str | None
    totp_enabled: bool

    def has_password_hash(self, verified_hash: str | None) -> bool:
        """Whether the password that was just verified is still the user's."""
        return self.password_hash is not None and self.password_hash == verified_hash


async def lock_user_for_sign_in(db: AsyncSession, user_id: uuid.UUID) -> LockedUser | None:
    """Lock the user row and read the current password hash and two-factor state, or None.

    Call it after the first step of a sign-in held (a password verified, a provider vouching) and
    before deciding what the sign-in gets. The lock lasts until the caller's transaction ends, so
    the session (or the challenge) is committed while no reset or enrolment can slip in between.
    Decide from the result, not from a user read before it: if a reset committed since, the
    password sign-in must be refused as if the password had been wrong, and if two-factor
    authentication was turned on, no session may be created without the second step.
    """
    row = (
        await db.execute(
            select(User.password_hash, User.totp_enabled_at)
            .where(User.id == user_id)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if row is None:
        return None
    return LockedUser(password_hash=row.password_hash, totp_enabled=row.totp_enabled_at is not None)
