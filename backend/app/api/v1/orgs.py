"""Organizations, members, invites and the audit log."""

import math
import uuid
from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.api.deps import Access, CurrentSession, DbSession, SettingsDep, client_ip, require, utcnow
from app.api.schemas import (
    AuditEventOut,
    InviteAcceptIn,
    InviteCreatedOut,
    InviteCreateIn,
    InviteOrgOut,
    InviteOut,
    InvitePreviewOut,
    MemberOut,
    MembershipOut,
    MemberUpdateIn,
    OrgCreateIn,
    OrgDeleteIn,
    OrgOut,
    OrgUpdateIn,
    OrgWithRoleOut,
    Page,
    UserOut,
)
from app.auth.totp_service import lock_user
from app.config import Settings
from app.core.errors import (
    confirmation_mismatch,
    conflict,
    forbidden,
    not_configured,
    not_found,
    too_many_requests,
)
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission, can_assign_role
from app.core.security import mark_uncacheable, new_token, token_digest
from app.db.models import (
    AuditAction,
    AuditEvent,
    Invite,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from app.exports.audit import (
    MAX_AUDIT_EXPORT_ROWS,
    AuditQuery,
    count_events_up_to,
    stream_audit_csv,
    too_large,
)
from app.services.audit import record_audit
from app.services.deletion import delete_organization, lock_organization
from app.services.invites import check_invite_email_limit, enqueue_invite_email
from app.services.slugs import RESERVED_ORG_SLUGS, slugify, with_random_suffix

router = APIRouter(tags=["orgs"])
logger = structlog.get_logger(__name__)

INVITE_LIFETIME = timedelta(days=7)

OrgReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
MemberManager = Annotated[Access, Depends(require(Permission.MEMBER_MANAGE))]
AuditReader = Annotated[Access, Depends(require(Permission.AUDIT_READ))]
OrgUpdater = Annotated[Access, Depends(require(Permission.ORG_UPDATE))]
OrgDeleter = Annotated[Access, Depends(require(Permission.ORG_DELETE))]


@router.post("/orgs", status_code=status.HTTP_201_CREATED, response_model=OrgOut)
async def create_org(
    body: OrgCreateIn, request: Request, auth: CurrentSession, db: DbSession
) -> Organization:
    org = await _insert_org(db, body.name)
    db.add(Membership(org_id=org.id, user_id=auth.user.id, role=MembershipRole.OWNER))
    await record_audit(
        db,
        org_id=org.id,
        actor_user_id=auth.user.id,
        action=AuditAction.ORG_CREATE,
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
        metadata={"name": org.name},
    )
    await db.commit()
    return org


async def _insert_org(db: DbSession, name: str) -> Organization:
    """Insert with a slug derived from the name, adding a suffix on collision.

    A slug that is also a top-level dashboard path (`login`, `api`, ...) is never handed out, so
    the first candidate for such a name already carries a suffix.
    """
    slug = slugify(name, fallback="org")
    for attempt in range(5):
        candidate = (
            slug if attempt == 0 and slug not in RESERVED_ORG_SLUGS else with_random_suffix(slug)
        )
        org = Organization(name=name, slug=candidate)
        try:
            async with db.begin_nested():
                db.add(org)
        except IntegrityError:
            continue
        return org
    raise conflict("SLUG_UNAVAILABLE", "Could not allocate a unique slug; try another name.")


@router.get("/orgs/{org_id}", response_model=OrgWithRoleOut)
async def get_org(org_id: uuid.UUID, access: OrgReader) -> OrgWithRoleOut:
    return OrgWithRoleOut(
        id=access.org.id,
        name=access.org.name,
        slug=access.org.slug,
        is_demo=access.org.is_demo,
        require_2fa=access.org.require_2fa,
        role=access.role,
    )


@router.patch("/orgs/{org_id}", response_model=OrgOut)
async def update_org(
    org_id: uuid.UUID,
    body: OrgUpdateIn,
    request: Request,
    access: OrgUpdater,
    db: DbSession,
    settings: SettingsDep,
) -> Organization:
    """Rename the organization, or require (or stop requiring) two-factor authentication.

    `name` needs `org:update` (admins and owners) and `require_2fa` needs `org:security` (owners);
    a request with both needs both, and is refused whole. Only values that change are applied and
    audited, in one `org.update` event, so repeating a request is a no-op.
    """
    if body.require_2fa is not None and not access.can(Permission.ORG_SECURITY):
        raise forbidden()
    if access.org.is_demo:
        raise forbidden("The demo organization cannot be changed.")

    # Locked: two people changing the organization at once cannot write two audit events that
    # disagree about what it was before.
    org = await lock_organization(db, access.org.id)
    if org is None:
        raise not_found()

    changes: dict[str, dict[str, Any]] = {}
    if body.name is not None and body.name != org.name:
        changes["name"] = {"from": org.name, "to": body.name}
    if body.require_2fa is not None and body.require_2fa != org.require_2fa:
        changes["require_2fa"] = {"from": org.require_2fa, "to": body.require_2fa}
    if not changes:
        return org

    if body.require_2fa and "require_2fa" in changes:
        await _check_owner_can_require_two_factor(db, settings, access.user_id)
    for field, change in changes.items():
        setattr(org, field, change["to"])
    await record_audit(
        db,
        org_id=org.id,
        actor_user_id=access.user_id,
        action=AuditAction.ORG_UPDATE,
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
        metadata=changes,
    )
    await db.commit()
    return org


async def _check_owner_can_require_two_factor(
    db: DbSession, settings: Settings, owner_id: uuid.UUID
) -> None:
    """Refuse to turn the requirement on unless it can work and its owner meets it.

    Turning it on locks every member without two-factor authentication out of the org's routes
    until they enable it. So the owner who does it must be able to meet the rule, and two-factor
    authentication needs `CREDENTIALS_KEYS`. Turning it off needs neither.
    """
    if not settings.is_crypto_configured:
        raise not_configured(
            "Requiring two-factor authentication needs CREDENTIALS_KEYS to be set; "
            "generate a key with `openssl rand -base64 32` and set it as `<key id>:<key>`."
        )
    # The owner's own row is locked too: they cannot turn their own two-factor
    # authentication off in another request between this check and the commit.
    owner = await lock_user(db, owner_id)
    if owner is None or not owner.totp_enabled:
        raise conflict(
            "TWO_FACTOR_NOT_ENABLED",
            "Turn on two-factor authentication for your own account before requiring it.",
        )


@router.delete("/orgs/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: uuid.UUID, body: OrgDeleteIn, access: OrgDeleter, db: DbSession
) -> None:
    """Delete the organization with its projects, traces, keys, members, invites and audit log.

    Only an owner may, and only by typing the organization's slug as `confirm`. Everything goes
    in one transaction: it is all deleted or none of it is. This is synchronous, which is fine for
    the size of organization this runs; a very large one needs a background job.
    """
    if access.org.is_demo:
        raise forbidden("The demo organization cannot be deleted.")
    org = await lock_organization(db, access.org.id)
    if org is None:
        raise not_found()
    if body.confirm != org.slug:
        raise confirmation_mismatch("`confirm` must be the organization's slug.")

    project_ids = await delete_organization(db, org.id)
    await db.commit()
    # After the commit, so a rolled-back delete is never reported. Ids only.
    logger.info(
        "org_deleted",
        org_id=str(org.id),
        project_ids=[str(project_id) for project_id in project_ids],
        actor_user_id=str(access.user_id),
    )


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
async def list_members(org_id: uuid.UUID, access: OrgReader, db: DbSession) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == access.org.id)
            .order_by(Membership.created_at, User.id)
        )
    ).all()
    return [
        MemberOut(
            user=UserOut.model_validate(user),
            role=membership.role,
            created_at=membership.created_at,
        )
        for user, membership in rows
    ]


async def _locked_membership(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = await db.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .with_for_update()
    )
    if membership is None:
        raise not_found()
    return membership


async def _ensure_not_last_owner(db: DbSession, membership: Membership) -> None:
    """Refuse to demote or remove the only owner.

    Owner rows are locked, so two concurrent demotions cannot both succeed.
    """
    if membership.role is not MembershipRole.OWNER:
        return
    owners = (
        await db.scalars(
            select(Membership.user_id)
            .where(Membership.org_id == membership.org_id, Membership.role == MembershipRole.OWNER)
            .with_for_update()
        )
    ).all()
    if len(owners) <= 1:
        raise conflict("LAST_OWNER", "An organization must keep at least one owner.")


@router.patch("/orgs/{org_id}/members/{user_id}", response_model=MemberOut)
async def update_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberUpdateIn,
    request: Request,
    access: MemberManager,
    db: DbSession,
) -> MemberOut:
    membership = await _locked_membership(db, access.org.id, user_id)
    if not can_assign_role(access.role, membership.role) or not can_assign_role(
        access.role, body.role
    ):
        raise forbidden("Only owners can grant or change the owner role.")

    previous_role = membership.role
    if previous_role is not body.role:
        if body.role is not MembershipRole.OWNER:
            await _ensure_not_last_owner(db, membership)
        membership.role = body.role
        await record_audit(
            db,
            org_id=access.org.id,
            actor_user_id=access.user_id,
            action=AuditAction.MEMBER_ROLE_CHANGE,
            target_type="user",
            target_id=user_id,
            ip=client_ip(request),
            metadata={"from": previous_role.value, "to": body.role.value},
        )
    await db.commit()

    user = await db.get_one(User, user_id)
    return MemberOut(
        user=UserOut.model_validate(user), role=membership.role, created_at=membership.created_at
    )


@router.delete("/orgs/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    access: OrgReader,
    db: DbSession,
) -> None:
    leaving_self = user_id == access.user_id
    if not leaving_self and not access.can(Permission.MEMBER_MANAGE):
        raise forbidden()

    membership = await _locked_membership(db, access.org.id, user_id)
    if not leaving_self and not can_assign_role(access.role, membership.role):
        raise forbidden("Only owners can remove an owner.")
    await _ensure_not_last_owner(db, membership)

    await db.delete(membership)
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.MEMBER_REMOVE,
        target_type="user",
        target_id=user_id,
        ip=client_ip(request),
        metadata={"role": membership.role.value},
    )
    await db.commit()


# --- invites --------------------------------------------------------------------


@router.post(
    "/orgs/{org_id}/invites", status_code=status.HTTP_201_CREATED, response_model=InviteCreatedOut
)
async def create_invite(
    org_id: uuid.UUID,
    body: InviteCreateIn,
    request: Request,
    response: Response,
    access: MemberManager,
    db: DbSession,
    settings: SettingsDep,
) -> InviteCreatedOut:
    """Create an invite and return its link; with an `email` and a sender, also mail it.

    The invite, its audit event and the queued email commit in one transaction. Mailing only
    changes how the link reaches someone: the invite is still accepted by any signed-in holder
    of the link, whatever address it was mailed to.
    """
    if not can_assign_role(access.role, body.role):
        raise forbidden("Only owners can invite owners.")

    mail_to = body.email if settings.is_email_configured else None
    if mail_to is not None:
        # Checked before anything is written, so a refused request leaves no invite behind.
        retry_after = await check_invite_email_limit(db, access.org.id, access.user_id)
        if retry_after is not None:
            raise too_many_requests(
                math.ceil(retry_after),
                "Too many invitation emails sent. Try again later.",
            )

    token = new_token()
    url = f"{settings.app_base_url}/invite/{token}"
    invite = Invite(
        org_id=access.org.id,
        role=body.role,
        email=body.email,
        token_hash=token_digest(token),
        created_by=access.user_id,
        expires_at=utcnow() + INVITE_LIFETIME,
    )
    db.add(invite)
    await db.flush()
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.INVITE_CREATE,
        target_type="invite",
        target_id=invite.id,
        ip=client_ip(request),
        metadata={"role": body.role.value},
    )
    if mail_to is not None:
        await enqueue_invite_email(
            db,
            to=mail_to,
            org_name=access.org.name,
            role=body.role.value,
            url=url,
            inviter_name=access.auth.user.name,
        )
    await db.commit()
    mark_uncacheable(response)  # the link carries the invite token
    return InviteCreatedOut(
        id=invite.id,
        role=invite.role,
        email=invite.email,
        url=url,
        expires_at=invite.expires_at,
    )


@router.get("/orgs/{org_id}/invites", response_model=list[InviteOut])
async def list_invites(org_id: uuid.UUID, access: MemberManager, db: DbSession) -> list[Invite]:
    invites = await db.scalars(
        select(Invite)
        .where(
            Invite.org_id == access.org.id,
            Invite.accepted_at.is_(None),
            Invite.expires_at > utcnow(),
        )
        .order_by(Invite.created_at.desc())
    )
    return list(invites.all())


@router.delete("/orgs/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    org_id: uuid.UUID,
    invite_id: uuid.UUID,
    request: Request,
    access: MemberManager,
    db: DbSession,
) -> None:
    invite = await db.get(Invite, invite_id)
    if invite is None or invite.org_id != access.org.id or invite.accepted_at is not None:
        raise not_found()
    await db.delete(invite)
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.INVITE_REVOKE,
        target_type="invite",
        target_id=invite.id,
        ip=client_ip(request),
    )
    await db.commit()


@router.get("/invites/preview", response_model=InvitePreviewOut)
async def preview_invite(
    token: Annotated[str, Query(min_length=1, max_length=128)],
    _auth: CurrentSession,  # only signed-in users may look up an invite
    db: DbSession,
) -> InvitePreviewOut:
    """Show the org and role an invite grants, without accepting it.

    Requires a signed-in user (the invitee), like accepting does. Unknown, used and expired
    tokens all get the same 404, so the endpoint does not reveal which invites exist.
    """
    invite = await db.scalar(select(Invite).where(Invite.token_hash == token_digest(token)))
    if invite is None or invite.accepted_at is not None or invite.expires_at <= utcnow():
        raise not_found("This invite is invalid or has expired.")

    org = await db.get_one(Organization, invite.org_id)
    return InvitePreviewOut(
        org=InviteOrgOut.model_validate(org), role=invite.role, expires_at=invite.expires_at
    )


@router.post("/invites/accept", response_model=MembershipOut)
async def accept_invite(
    body: InviteAcceptIn, request: Request, auth: CurrentSession, db: DbSession
) -> MembershipOut:
    now = utcnow()
    invite = await db.scalar(
        select(Invite).where(Invite.token_hash == token_digest(body.token)).with_for_update()
    )
    if invite is None or invite.accepted_at is not None or invite.expires_at <= now:
        raise not_found("This invite is invalid or has expired.")

    org = await db.get_one(Organization, invite.org_id)

    existing = await db.get(Membership, (invite.org_id, auth.user.id))
    if existing is None:
        role = invite.role
        db.add(Membership(org_id=invite.org_id, user_id=auth.user.id, role=role))
    else:
        # Accepting never downgrades someone who is already a member.
        role = existing.role

    invite.accepted_at = now
    await record_audit(
        db,
        org_id=invite.org_id,
        actor_user_id=auth.user.id,
        action=AuditAction.INVITE_ACCEPT,
        target_type="invite",
        target_id=invite.id,
        ip=client_ip(request),
        metadata={"role": role.value, "already_member": existing is not None},
    )
    await db.commit()
    return MembershipOut(org=OrgOut.model_validate(org), role=role)


# --- audit log ------------------------------------------------------------------


@router.get("/orgs/{org_id}/audit", response_model=Page[AuditEventOut])
async def list_audit_events(
    org_id: uuid.UUID,
    access: AuditReader,
    db: DbSession,
    filters: AuditQuery,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[AuditEventOut]:
    actor = aliased(User)
    query = (
        select(AuditEvent, actor)
        .outerjoin(actor, actor.id == AuditEvent.actor_user_id)
        .where(*filters.conditions(access.org.id))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        created_at, event_id = decode_uuid_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.created_at < created_at,
                and_(AuditEvent.created_at == created_at, AuditEvent.id < event_id),
            )
        )

    rows = (await db.execute(query)).all()
    page = rows[:limit]
    items = [
        AuditEventOut(
            id=event.id,
            action=event.action,
            actor=UserOut.model_validate(user) if user else None,
            target_type=event.target_type,
            target_id=event.target_id,
            metadata=event.metadata_,
            ip=str(event.ip) if event.ip else None,
            created_at=event.created_at,
        )
        for event, user in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last_event = page[-1][0]
        next_cursor = encode_cursor(last_event.created_at, str(last_event.id))
    return Page[AuditEventOut](items=items, next_cursor=next_cursor)


@router.get(
    "/orgs/{org_id}/audit/export.csv",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/csv": {}}, "description": "The audit log as CSV."},
        422: {"description": "More than 50 000 events match (`EXPORT_TOO_LARGE`)."},
    },
    summary="Download the audit log as CSV",
)
async def export_audit_csv(
    org_id: uuid.UUID,
    request: Request,
    access: AuditReader,
    db: DbSession,
    filters: AuditQuery,
) -> StreamingResponse:
    """The audit log, filtered like `GET /orgs/{org_id}/audit`, streamed as CSV (newest first).

    A log with more than 50 000 matching events is refused before anything is sent.
    """
    audited_org_id = access.org.id  # read now: the rollback below expires loaded objects
    if await count_events_up_to(db, audited_org_id, filters, MAX_AUDIT_EXPORT_ROWS) > (
        MAX_AUDIT_EXPORT_ROWS
    ):
        raise too_large()
    # Hand the connection back now: the body is read with sessions of its own and can take a
    # while to send, which must not hold a pooled connection.
    await db.rollback()
    filename = f"audit-log-{utcnow():%Y%m%d}.csv"
    return StreamingResponse(
        stream_audit_csv(request.app.state.session_factory, audited_org_id, filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
