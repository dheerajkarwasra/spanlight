"""What a provider profile is allowed to do: sign someone in, create an account, link, unlink.

The rules, in the order they are applied to a sign-in:

1. An identity with this `(provider, subject)` exists: it is that user. The subject is the
   provider's stable id; the email is not consulted, so a changed address cannot move an
   identity to another account.
2. Otherwise, if a user has the same email:
   * the provider does not vouch for the email: refused, nothing linked;
   * the local account never proved the email: refused, nothing linked;
   * both vouch for it: the identity is linked to that user.
3. Otherwise, if the provider vouches for the email, a new user is created. An email the
   provider does not vouch for never creates an account: GitHub lets anyone add any address to
   their account unverified, and an account created for it would hold that GitHub identity after
   the real owner of the address recovered the account.

"Vouch" always means the provider's verified flag, never the bare presence of an address.

Callers commit. Nothing here sets cookies or builds redirects.
"""

import enum
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth_providers import OAuthProfile
from app.db.errors import violated_constraint
from app.db.models import AuditAction, OAuthIdentity, User
from app.services.audit import record_user_audit_in_each_org
from app.services.demo import is_demo_user

logger = structlog.get_logger(__name__)

MAX_NAME_LENGTH = 100

# The unique constraints a lost race trips over: two requests creating the same user, or the same
# identity. They are named in `0005_add_oauth_identities` and the initial schema, and
# `tests/db/test_oauth_identities.py` checks the names exist.
_USER_EMAIL_KEY = "users_email_key"
_IDENTITY_SUBJECT_KEY = "oauth_identities_provider_subject_key"
_IDENTITY_USER_PROVIDER_KEY = "oauth_identities_user_id_provider_key"
_SIGN_IN_RACE_CONSTRAINTS = frozenset(
    {_USER_EMAIL_KEY, _IDENTITY_SUBJECT_KEY, _IDENTITY_USER_PROVIDER_KEY}
)
_LINK_RACE_CONSTRAINTS = frozenset({_IDENTITY_SUBJECT_KEY, _IDENTITY_USER_PROVIDER_KEY})


class OAuthCode(enum.StrEnum):
    """The `error` values an OAuth callback can redirect with (never a problem+json body)."""

    STATE = "OAUTH_STATE"
    PROVIDER_ERROR = "OAUTH_PROVIDER_ERROR"
    EMAIL_UNVERIFIED_AT_PROVIDER = "EMAIL_UNVERIFIED_AT_PROVIDER"
    ACCOUNT_EMAIL_UNVERIFIED = "ACCOUNT_EMAIL_UNVERIFIED"
    ALREADY_LINKED = "OAUTH_ALREADY_LINKED"


class OAuthRefused(Exception):  # noqa: N818 - the name the interface fixes
    """The sign-in or link must not go ahead. `code` is what the browser is told."""

    def __init__(self, code: OAuthCode) -> None:
        super().__init__(code.value)
        self.code = code


class IdentityNotLinkedError(Exception):
    """The user has no account at that provider linked."""


class LastSignInMethodError(Exception):
    """Unlinking would leave the user with no way to sign in."""


async def sign_in_with_profile(
    db: AsyncSession, profile: OAuthProfile, *, ip: str | None = None
) -> User:
    """The user this profile signs in, creating or linking one as the module docstring says.

    Raises `OAuthRefused` when nobody may be signed in. The caller commits and then creates the
    session.
    """
    try:
        async with db.begin_nested():
            return await _resolve(db, profile, ip)
    except IntegrityError as error:
        # A concurrent callback for the same account committed first (two tabs, a double click)
        # and won the unique constraint on the user's email or on the identity. What it wrote is
        # visible now, so the second pass finds it and signs this request in as the same user.
        # Any other integrity error is a bug or a bad row, not a race, and is not retried.
        if violated_constraint(error) not in _SIGN_IN_RACE_CONSTRAINTS:
            raise
        logger.info("oauth_sign_in_race", provider=profile.provider)
    return await _resolve(db, profile, ip)


async def link_identity(
    db: AsyncSession,
    user: User,
    profile: OAuthProfile,
    *,
    require_verified_email: bool,
    ip: str | None = None,
) -> bool:
    """Attach the provider account to `user` after the caller confirmed account ownership.

    The provider's email is not checked: the caller already confirmed account ownership, and
    sign-in never goes by this email. Return whether an identity was attached; linking the
    account that is already linked to this user is a no-op.

    Raises `OAuthRefused`:

    * `ACCOUNT_EMAIL_UNVERIFIED` for the shared demo account, and for an account whose email is
      not verified when `require_verified_email` is set. Anyone can register an address that is
      not theirs, and an identity attached to that unproven account would stay on it after the
      address's real owner recovered it with a password reset. Callers set the flag when email
      is configured; without email nobody can verify an address or run that recovery, and
      refusing would lock the feature out of such installations.
    * `ALREADY_LINKED` if the provider account belongs to someone else, or this user already
      has a different account of that provider.
    """
    if is_demo_user(user) or (require_verified_email and not user.email_verified):
        raise OAuthRefused(OAuthCode.ACCOUNT_EMAIL_UNVERIFIED)
    now = datetime.now(UTC)
    try:
        async with db.begin_nested():
            existing = await _identity_by_subject(db, profile)
            if existing is not None:
                if existing.user_id != user.id:
                    raise OAuthRefused(OAuthCode.ALREADY_LINKED)
                existing.last_used_at = now
                return False
            await _attach(db, user, profile, now=now, ip=ip, via="link")
    except IntegrityError as error:
        # Lost a race to link the same provider account (or this user's slot for the provider);
        # whoever won owns it. Any other integrity error is not a race and propagates.
        if violated_constraint(error) not in _LINK_RACE_CONSTRAINTS:
            raise
        raise OAuthRefused(OAuthCode.ALREADY_LINKED) from error

    return True


async def unlink_identity(
    db: AsyncSession, user: User, provider: str, *, ip: str | None = None
) -> None:
    """Remove the user's account at `provider`, unless it is their last way to sign in.

    Raises `IdentityNotLinkedError` or `LastSignInMethodError`.
    """
    # Serialise unlinks for this user. Without the lock, two requests removing the user's two
    # identities could each still see the other one, both pass the check below, and leave an
    # account nobody can sign in to. The second waits here and then counts what the first left.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    identities = (
        await db.scalars(select(OAuthIdentity).where(OAuthIdentity.user_id == user.id))
    ).all()
    target = next((identity for identity in identities if identity.provider == provider), None)
    if target is None:
        raise IdentityNotLinkedError
    if not user.has_password and len(identities) == 1:
        raise LastSignInMethodError

    await db.delete(target)
    await db.flush()
    await record_user_audit_in_each_org(
        db,
        user_id=user.id,
        action=AuditAction.USER_OAUTH_UNLINK,
        ip=ip,
        metadata={"provider": provider},
    )


async def drop_identities(
    db: AsyncSession, user_id: uuid.UUID, *, reason: str, ip: str | None = None
) -> int:
    """Unlink every provider account of the user, and audit each as `user.oauth_unlink`.

    For a change that makes earlier links untrustworthy rather than a choice of the user, so
    it does not stop at the user's last way to sign in. Returns how many were removed. The
    caller commits.
    """
    identities = (
        await db.scalars(
            select(OAuthIdentity)
            .where(OAuthIdentity.user_id == user_id)
            .order_by(OAuthIdentity.created_at, OAuthIdentity.provider)
        )
    ).all()
    providers = [identity.provider for identity in identities]
    for identity in identities:
        await db.delete(identity)
    await db.flush()
    for provider in providers:
        await record_user_audit_in_each_org(
            db,
            user_id=user_id,
            action=AuditAction.USER_OAUTH_UNLINK,
            ip=ip,
            metadata={"provider": provider, "reason": reason},
        )
    return len(providers)


async def _resolve(db: AsyncSession, profile: OAuthProfile, ip: str | None) -> User:
    now = datetime.now(UTC)

    identity = await _identity_by_subject(db, profile)
    if identity is not None:
        identity.last_used_at = now
        if profile.email is not None and profile.email_verified:
            identity.email = profile.email
        return await _user(db, identity.user_id)

    existing = await _user_by_email(db, profile.email)
    if existing is not None:
        if not profile.email_verified:
            raise OAuthRefused(OAuthCode.EMAIL_UNVERIFIED_AT_PROVIDER)
        if not existing.email_verified or is_demo_user(existing):
            raise OAuthRefused(OAuthCode.ACCOUNT_EMAIL_UNVERIFIED)
        await _attach(db, existing, profile, now=now, ip=ip, via="email_match")
        return existing

    if profile.email is None or not profile.email_verified:
        raise OAuthRefused(OAuthCode.EMAIL_UNVERIFIED_AT_PROVIDER)
    user = User(
        email=profile.email,
        password_hash=None,
        name=_display_name(profile.name, profile.email),
        email_verified_at=now,
    )
    db.add(user)
    await db.flush()
    db.add(_identity(user.id, profile, now))
    await db.flush()
    return user


async def _attach(
    db: AsyncSession, user: User, profile: OAuthProfile, *, now: datetime, ip: str | None, via: str
) -> None:
    """Link the provider account to an existing user and audit it."""
    same_provider = await db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.user_id == user.id, OAuthIdentity.provider == profile.provider
        )
    )
    if same_provider is not None:
        if same_provider.subject != profile.subject:
            # Another account of the same provider is already linked; this one would be a second
            # way in that the user never asked for.
            raise OAuthRefused(OAuthCode.ALREADY_LINKED)
        # This very account was linked a moment ago by a concurrent request; nothing to add.
        same_provider.last_used_at = now
        return
    db.add(_identity(user.id, profile, now))
    await db.flush()
    await record_user_audit_in_each_org(
        db,
        user_id=user.id,
        action=AuditAction.USER_OAUTH_LINK,
        ip=ip,
        metadata={"provider": profile.provider, "via": via},
    )


def _identity(user_id: uuid.UUID, profile: OAuthProfile, now: datetime) -> OAuthIdentity:
    return OAuthIdentity(
        user_id=user_id,
        provider=profile.provider,
        subject=profile.subject,
        email=profile.email,
        created_at=now,
        last_used_at=now,
    )


async def _identity_by_subject(db: AsyncSession, profile: OAuthProfile) -> OAuthIdentity | None:
    return await db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == profile.provider, OAuthIdentity.subject == profile.subject
        )
    )


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    return (await db.scalars(select(User).where(User.id == user_id))).one()


async def _user_by_email(db: AsyncSession, email: str | None) -> User | None:
    if email is None:
        return None
    return await db.scalar(select(User).where(User.email == email))


def _display_name(name: str | None, email: str) -> str:
    """The provider's name, or the part of the email before the `@`; at most 100 characters."""
    candidate = (name or "").strip()[:MAX_NAME_LENGTH].strip()
    return candidate or email.split("@", 1)[0][:MAX_NAME_LENGTH]
