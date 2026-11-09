"""Member-removal, organization- and project-deletion notices in the change's transaction."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Membership, MembershipRole, Organization, Project, User
from app.email.templates import (
    render_member_removed_notice,
    render_org_deleted_notice,
    render_project_deleted_notice,
)
from app.notifications import NotificationKind, enqueue


async def enqueue_member_removed_notices(
    db: AsyncSession, *, org: Organization, actor: User, removed: User
) -> None:
    owners = await db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org.id,
            Membership.role == MembershipRole.OWNER,
            User.email_verified_at.is_not(None),
            User.id != actor.id,
            User.id != removed.id,
        )
    )
    message = render_member_removed_notice(
        org.name,
        f"{removed.name} ({removed.email})",
        None if actor.id == removed.id else f"{actor.name} ({actor.email})",
    )
    recipients = list(owners)
    # Self-leave also needs a verified address, so signups cannot relay notices to strangers.
    if removed.email_verified_at is not None:
        recipients.append(removed)
    for recipient in recipients:
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": recipient.email},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )


async def enqueue_org_deleted_notices(db: AsyncSession, *, org: Organization, actor: User) -> None:
    owners = await db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org.id,
            Membership.role == MembershipRole.OWNER,
            User.email_verified_at.is_not(None),
            User.id != actor.id,
        )
    )
    message = render_org_deleted_notice(org.name, f"{actor.name} ({actor.email})")
    for owner in owners:
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": owner.email},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )


async def enqueue_project_deleted_notices(
    db: AsyncSession, *, org: Organization, project: Project, actor: User
) -> None:
    owners = await db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org.id,
            Membership.role == MembershipRole.OWNER,
            User.email_verified_at.is_not(None),
            User.id != actor.id,
        )
    )
    message = render_project_deleted_notice(org.name, project.name, f"{actor.name} ({actor.email})")
    for owner in owners:
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": owner.email},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )
