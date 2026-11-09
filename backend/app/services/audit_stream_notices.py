"""Owner notices for audit-stream writes, queued in the write's transaction."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Membership, MembershipRole, Organization, User
from app.email.templates import render_audit_stream_notice
from app.notifications import NotificationKind, enqueue


def saved_stream_change(url: str, enabled: bool, previous: tuple[str, bool] | None) -> str | None:
    """What a save changed, for the notice; `None` when it changed nothing."""
    if previous is None:
        return "set up"
    changes = []
    if url != previous[0]:
        changes.append("URL changed")
    if enabled != previous[1]:
        changes.append("resumed" if enabled else "paused")
    return " and ".join(changes) or None


async def enqueue_audit_stream_notices(
    db: AsyncSession, *, org: Organization, actor: User, host: str, change: str
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
    message = render_audit_stream_notice(
        org.name, f"{actor.name} ({actor.email})", f"{change} (receiver: {host})"
    )
    for owner in owners:
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": owner.email},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )
