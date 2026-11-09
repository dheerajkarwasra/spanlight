"""Mailing an invite: the email, and the limit that keeps it from being abused.

The route in `app.api.v1.orgs` creates the invite; the rules for sending it live here. Sign-up is
open, so anyone can create an org and invite arbitrary addresses. Without a limit, strangers could
send mail from our domain, so mailed invites are counted per org and per inviting user. The user
limit holds across orgs: without it, creating many orgs would multiply the per-org budget.
"""

import dataclasses
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.throttle import check_and_record
from app.db.models import Invite, Membership, MembershipRole, Organization, User
from app.email.templates import render_invite, render_member_joined_notice
from app.notifications import NotificationKind, enqueue

INVITE_EMAIL_SCOPE = "invite_email"
MAX_INVITE_EMAILS_PER_HOUR = 20
INVITE_EMAIL_USER_SCOPE = "invite_email_user"
MAX_INVITE_EMAILS_PER_USER_PER_HOUR = 50
INVITE_EMAIL_WINDOW = timedelta(hours=1)


async def check_invite_email_limit(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> float | None:
    """Count a mailed invite against the org's and the inviter's budgets.

    Returns the seconds to wait if either budget is used up, else None.

    Only invites that are actually mailed are counted: a link shared by hand sends nothing.
    The counts are recorded in the caller's transaction, so a request that fails afterwards, or
    is refused by the second budget, and rolls back does not use up a slot in either.
    """
    org_wait = await check_and_record(
        db, INVITE_EMAIL_SCOPE, str(org_id), MAX_INVITE_EMAILS_PER_HOUR, INVITE_EMAIL_WINDOW
    )
    if org_wait is not None:
        return org_wait
    return await check_and_record(
        db,
        INVITE_EMAIL_USER_SCOPE,
        str(user_id),
        MAX_INVITE_EMAILS_PER_USER_PER_HOUR,
        INVITE_EMAIL_WINDOW,
    )


async def enqueue_invite_email(
    db: AsyncSession, *, to: str, org_name: str, role: str, url: str, inviter_name: str
) -> None:
    """Queue the invitation email in the caller's transaction.

    The invite and the queued email commit together, so there is never an email whose link does
    not exist. The caller checks `settings.is_email_configured` and `check_invite_email_limit`
    first.
    """
    message = dataclasses.replace(render_invite(org_name, role, url, inviter_name), to=to)
    await enqueue(
        db,
        NotificationKind.EMAIL,
        target={"to": message.to},
        payload={"subject": message.subject, "text": message.text, "html": message.html},
    )


async def enqueue_member_joined_notices(
    db: AsyncSession, *, org: Organization, invite: Invite, member: User, role: MembershipRole
) -> None:
    owners = await db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org.id,
            Membership.role == MembershipRole.OWNER,
            User.email_verified_at.is_not(None),
            User.id != member.id,
        )
    )
    inviter = await db.get(User, invite.created_by) if invite.created_by is not None else None
    invited_by = f"{inviter.name} ({inviter.email})" if inviter else "a former member"
    message = render_member_joined_notice(
        org.name, f"{member.name} ({member.email})", role.value, invited_by
    )
    for owner in owners:
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": owner.email},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )
