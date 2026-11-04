"""Single-use tokens for links sent by email (verify an address, reset a password, change the
address).

The raw token is 32 random bytes in URL-safe base64. It travels in the emailed link, and only
its SHA-256 is stored in `email_tokens`, so reading that table cannot be turned into working
links. The raw link does exist in one other place: the body of the queued email in
`notification_outbox`. It stays there until the delivery job settles the row (sent, or failed for
good) and reduces the payload to its subject, so a database read can recover a working link
only while its email is still waiting to be delivered.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import new_token, token_digest
from app.db.models import EmailToken, EmailTokenKind, User


async def issue_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    kind: EmailTokenKind,
    ttl: timedelta,
    *,
    new_email: str | None = None,
) -> str:
    """Create a token of `kind` for the user and return its raw value. An `email_change` token
    carries the address it moves the user to in `new_email`.

    The row joins the caller's transaction (flushed, not committed), so the token and whatever
    sends it commit or roll back together.
    """
    raw = new_token()
    now = datetime.now(UTC)
    db.add(
        EmailToken(
            user_id=user_id,
            kind=kind,
            token_hash=token_digest(raw),
            created_at=now,
            expires_at=now + ttl,
            new_email=new_email,
        )
    )
    await db.flush()
    return raw


async def retire_unused_tokens(db: AsyncSession, user_id: uuid.UUID, kind: EmailTokenKind) -> None:
    """Mark every unused token of `kind` for the user as used, so none of them can be spent.

    Run before issuing a new token (only the newest link should work) and after a successful
    reset (links mailed earlier must not outlive the password they were for). Expired tokens
    are included: it is harmless, and keeps the statement one plain condition. The caller
    commits.
    """
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user_id,
            EmailToken.kind == kind,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )


async def consume_token(db: AsyncSession, raw: str, kind: EmailTokenKind) -> User | None:
    """Spend a token and return its user, or `None` if it cannot be spent.

    A token is rejected when it is unknown, already used, expired or of another kind, and the
    caller cannot tell which: all four answer `None`. A rejected token is left as it was, so
    presenting a reset token to the verification route does not burn it.

    Marking the token used is one conditional `UPDATE`, which makes "spend it exactly once"
    atomic: of two concurrent requests with the same token, the second waits for the first's
    row lock, re-checks `used_at IS NULL`, finds it set, and gets `None`. The caller commits.
    """
    now = datetime.now(UTC)
    user_id = await db.scalar(
        update(EmailToken)
        .where(
            EmailToken.token_hash == token_digest(raw),
            EmailToken.kind == kind,
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(EmailToken.user_id)
    )
    if user_id is None:
        return None
    return await db.get(User, user_id)
