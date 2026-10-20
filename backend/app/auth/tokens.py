"""Personal access tokens for one user: issue, list, revoke, and look one up by what it presents.

A token is a long-lived credential for scripts and tools, so only the SHA-256 of its secret is
stored (see `app.core.security`) and the token itself leaves this module once, in `IssuedToken`.
Nothing here logs or raises with a secret in it. Callers commit.

Whether a token may be used, and for what, is not decided here: the authentication dependency
checks revocation and expiry, and `app.api.deps.require` checks the user's current memberships
and the token's scope on every request.
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    PAT_PREFIX,
    ParsedKey,
    generate_key,
    key_secret_matches,
    parse_key,
)
from app.db.models import PersonalAccessToken, TokenScope, User


@dataclass(frozen=True)
class IssuedToken:
    token: PersonalAccessToken
    plaintext: str  # `spl_pat_…`, the only copy there will ever be


@dataclass(frozen=True)
class PresentedToken:
    """A stored token whose secret was proved, with the user it acts as."""

    token: PersonalAccessToken
    user: User


class RevokeOutcome(enum.Enum):
    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"
    NOT_FOUND = "not_found"


def parse_token(credentials: str) -> ParsedKey | None:
    """The prefix and secret of a presented `spl_pat_…` credential, or None if it is not one."""
    return parse_key(credentials, PAT_PREFIX)


async def issue_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    name: str,
    scope: TokenScope,
    expires_at: datetime | None,
) -> IssuedToken:
    generated = generate_key(PAT_PREFIX)
    token = PersonalAccessToken(
        user_id=user_id,
        name=name,
        prefix=generated.prefix,
        secret_hash=generated.secret_hash,
        scope=scope,
        expires_at=expires_at,
    )
    db.add(token)
    await db.flush()
    return IssuedToken(token=token, plaintext=generated.plaintext)


async def list_active_tokens(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime
) -> list[PersonalAccessToken]:
    """The user's tokens that can still be used, newest first.

    A revoked or expired token is no use to anyone, so it is not listed; there is nothing the
    person could do with it that the list would help with.
    """
    tokens = await db.scalars(
        select(PersonalAccessToken)
        .where(
            PersonalAccessToken.user_id == user_id,
            PersonalAccessToken.revoked_at.is_(None),
            or_(PersonalAccessToken.expires_at.is_(None), PersonalAccessToken.expires_at > now),
        )
        .order_by(PersonalAccessToken.created_at.desc(), PersonalAccessToken.id.desc())
    )
    return list(tokens.all())


async def revoke_token(
    db: AsyncSession, user_id: uuid.UUID, token_id: uuid.UUID, *, now: datetime
) -> RevokeOutcome:
    """Revoke one of the user's tokens. Safe to repeat; another user's token is simply not found.

    The condition is part of the UPDATE, so of two concurrent revokes exactly one reports
    `REVOKED` and the first time of revocation is never overwritten.
    """
    revoked = await db.execute(
        update(PersonalAccessToken)
        .where(
            PersonalAccessToken.id == token_id,
            PersonalAccessToken.user_id == user_id,
            PersonalAccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .returning(PersonalAccessToken.id)
        .execution_options(synchronize_session=False)
    )
    if revoked.first() is not None:
        return RevokeOutcome.REVOKED
    exists = await db.scalar(
        select(PersonalAccessToken.id).where(
            PersonalAccessToken.id == token_id, PersonalAccessToken.user_id == user_id
        )
    )
    return RevokeOutcome.NOT_FOUND if exists is None else RevokeOutcome.ALREADY_REVOKED


async def find_presented_token(db: AsyncSession, parsed: ParsedKey) -> PresentedToken | None:
    """The token a request presents, if the prefix is known and the secret is right.

    Revoked and expired tokens are returned: the caller decides what to say about them, and says
    it only to a holder who proved the secret.
    """
    found = (
        await db.execute(
            select(PersonalAccessToken, User)
            .join(User, User.id == PersonalAccessToken.user_id)
            .where(PersonalAccessToken.prefix == parsed.prefix)
        )
    ).one_or_none()
    if found is None:
        return None
    token, user = found
    if not key_secret_matches(parsed.secret, token.secret_hash):
        return None
    return PresentedToken(token=token, user=user)


async def revoke_all_tokens(db: AsyncSession, user_id: uuid.UUID, *, now: datetime) -> int:
    """Revoke every token of the user's that still works; how many that was.

    A password reset calls this: a token minted from a stolen session must not outlive it.
    """
    revoked = await db.execute(
        update(PersonalAccessToken)
        .where(
            PersonalAccessToken.user_id == user_id,
            PersonalAccessToken.revoked_at.is_(None),
            or_(PersonalAccessToken.expires_at.is_(None), PersonalAccessToken.expires_at > now),
        )
        .values(revoked_at=now)
        .returning(PersonalAccessToken.id)
        .execution_options(synchronize_session=False)
    )
    return len(revoked.all())
