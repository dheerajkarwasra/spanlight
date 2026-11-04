"""Single-use email tokens, the throttle's event log, linked OAuth accounts, recovery codes and
personal access tokens."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, LargeBinary, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class EmailTokenKind(enum.StrEnum):
    VERIFY = "verify"
    RESET = "reset"
    EMAIL_CHANGE = "email_change"


class TokenScope(enum.StrEnum):
    READ = "read"  # may only use permissions classed `read` (see `app.core.permissions`)
    WRITE = "write"  # may use everything its user's role allows


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[EmailTokenKind] = mapped_column(_pg_enum(EmailTokenKind, "email_token_kind"))
    # SHA-256 of the raw token; the raw value only ever travels in the emailed link.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The address an `email_change` token moves the user to; NULL for every other kind. The
    # database does not check that pairing (see migration 1030): the app sets it.
    new_email: Mapped[str | None] = mapped_column(CITEXT)


class ThrottleEvent(Base):
    __tablename__ = "throttle_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthIdentity(Base):
    """A user's account at a sign-in provider (GitHub, Google): a way in without a password."""

    __tablename__ = "oauth_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    # The provider's stable id for the account (GitHub's numeric id, Google's `sub`). Sign-in
    # matches on this, never on the email, which the provider's user can change.
    subject: Mapped[str] = mapped_column(Text)
    # The address the provider reported when the account was linked or last signed in with a
    # verified address. Informational: it is shown to the user and never used to find a user.
    email: Mapped[str | None] = mapped_column(CITEXT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RecoveryCode(Base):
    """A one-time code that signs a user in when their authenticator app is lost.

    Only the SHA-256 of the code is stored; the code itself is shown once, when two-factor
    authentication is enabled. A used code stays as a row with `used_at` set. `code_length` is
    the length of the stored code (10 legacy, 28 current); NULL rows predate it and are legacy.
    """

    __tablename__ = "recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    code_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_length: Mapped[int | None] = mapped_column(SmallInteger)


class PersonalAccessToken(Base):
    """A credential a user creates for scripts and tools: it acts as them, within its scope.

    Only the SHA-256 of the secret is stored; the token itself is shown once, when it is created.
    It carries no permissions of its own: what it can reach is decided on every request from its
    user's current memberships, so removing a member ends their tokens' access at once.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    # `spl_pat_<12 chars>`: stored, shown and used for lookup. The secret follows it in the token.
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    scope: Mapped[TokenScope] = mapped_column(_pg_enum(TokenScope, "token_scope"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL: the token does not expire. A token past this time is refused with 401 `TOKEN_EXPIRED`.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
