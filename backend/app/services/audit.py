"""Audit trail writes. Callers commit as part of the audited change.

Who acted and from where comes from the request (`app.core.request_context.AuditContext`), so a
call site names only the action, the target and what changed. The table is append-only
(migration 1013); `purge_audit_events` is the one way rows leave it, apart from an org's deletion.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redact import REDACTED, redact_json
from app.core.request_context import AuditContext, current_audit_context, current_request_id
from app.db.models import AuditAction, AuditActorType, AuditEvent, Membership, Organization, User

Outcome = Literal["success", "failure"]

# A metadata key with one of these words in it holds a secret, whatever its value looks like.
_SECRET_KEY_WORDS = frozenset(
    {"secret", "password", "passwd", "token", "apikey", "authorization", "cookie", "hash"}
)
_KEY_WORDS = re.compile(r"[^a-z0-9]+")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    words = set(_KEY_WORDS.split(lowered))
    return bool(words & _SECRET_KEY_WORDS) or "api_key" in lowered or "private_key" in lowered


def redact_metadata(value: Any) -> Any:
    """Secrets out of audit metadata: by key name at any depth, then by value shape."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_secret_key(str(key)) else redact_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    return redact_json(value)


async def record_audit(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: AuditAction,
    target_type: str,
    target_id: str | uuid.UUID,
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_type: AuditActorType | None = None,
    project_id: uuid.UUID | None = None,
    outcome: Outcome = "success",
    reason: str | None = None,
) -> None:
    """Write one event. An event with no actor is the app's own (`system`) unless `actor_type`
    says otherwise; it carries only the request id, never the client of the request."""
    # Outside a request (worker, CLI) there is no context, which reads as knowing nothing.
    context = current_audit_context() or AuditContext()
    # The request's client is the actor's only when the actor is the one making the request: the
    # signed-in user, or the person a sign-in or password reset names (no principal yet).
    from_requester = actor_user_id is not None and context.user_id in (None, actor_user_id)
    authenticated = from_requester and context.user_id is not None
    if actor_type is None:
        if actor_user_id is None:
            actor_type = AuditActorType.SYSTEM
        elif authenticated and context.actor_type is not None:
            actor_type = AuditActorType(context.actor_type)
        else:
            actor_type = AuditActorType.USER
    actor_email = None
    if actor_user_id is not None:
        actor_email = (
            context.user_email
            if authenticated
            else await db.scalar(select(User.email).where(User.id == actor_user_id))
        )
    db.add(
        AuditEvent(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action.value,
            target_type=target_type,
            target_id=str(target_id),
            ip=ip or (context.ip if from_requester else None),
            metadata_=redact_metadata(metadata or {}),
            actor_type=actor_type.value,
            credential_id=context.credential_id if authenticated else None,
            actor_email=actor_email,
            project_id=project_id or context.project_id,
            outcome=outcome,
            reason=reason,
            user_agent=context.user_agent if from_requester else None,
            request_id=current_request_id(),
        )
    )
    await db.flush()


async def record_user_audit_in_each_org(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: AuditAction,
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
    target_type: str = "user",
    target_id: str | uuid.UUID | None = None,
    outcome: Outcome = "success",
    reason: str | None = None,
) -> None:
    """Audit a change to a person's own account in every org they belong to.

    Account changes (a password reset, a sign-in, a token) belong to no single org, and each org's
    audit log should show what happened to its members. The user is the actor and, unless named,
    the target. A user in no org leaves no event: the audit log is per org. Demo orgs are skipped:
    their one account is every visitor.
    """
    org_ids = (
        await db.scalars(
            select(Membership.org_id)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user_id, Organization.is_demo.is_(False))
            # One order for every writer: each event takes its org's chain lock (migration 1014),
            # so two transactions locking the same orgs in different orders would deadlock.
            .order_by(Membership.org_id)
        )
    ).all()
    for org_id in org_ids:
        await record_audit(
            db,
            org_id=org_id,
            actor_user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id if target_id is not None else user_id,
            ip=ip,
            metadata=metadata,
            outcome=outcome,
            reason=reason,
        )


async def purge_audit_events(db: AsyncSession, *, before: datetime, batch_size: int) -> int:
    """Delete up to `batch_size` events older than `before`; the caller commits.

    The append-only trigger lets a DELETE through only with `spanlight.audit_purge` set, and
    `SET LOCAL` keeps it to this transaction.
    """
    await db.execute(text("SET LOCAL spanlight.audit_purge = 'on'"))
    # shortcut: no index on `created_at` alone, so this scans the table; fine at thousands of
    # events per org a month, add `(created_at)` when the purge shows in slow-query logs.
    oldest = (
        select(AuditEvent.id)
        .where(AuditEvent.created_at < before)
        .order_by(AuditEvent.created_at)
        .limit(batch_size)
    )
    result = await db.execute(
        delete(AuditEvent)
        .where(AuditEvent.id.in_(oldest.scalar_subquery()))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))


@dataclass(frozen=True)
class ChainCheck:
    """What `check_audit_chain` found for one organization's events."""

    # Events in the chain, and the first and last `seq` still stored (retention removes the oldest).
    checked: int
    first_seq: int | None
    head_seq: int | None
    # The newest event's hash: record it elsewhere to detect a later removal of recent events.
    head_hash: bytes | None
    # The first event whose hash does not match its content, whose `prev_hash` is not the
    # previous event's hash, or that follows a gap in `seq`; None when the chain is intact.
    first_broken_seq: int | None

    @property
    def ok(self) -> bool:
        return self.first_broken_seq is None


_CHECK_CHAIN = text(
    """
    WITH chain AS (
        SELECT
            seq,
            row_hash,
            prev_hash,
            audit_event_digest(e) AS expected,
            lag(row_hash) OVER (ORDER BY seq) AS previous_hash,
            lag(seq) OVER (ORDER BY seq) AS previous_seq
        FROM audit_events e
        WHERE org_id = :org_id AND seq IS NOT NULL
    )
    SELECT
        count(*) AS checked,
        min(seq) AS first_seq,
        max(seq) AS head_seq,
        (array_agg(row_hash ORDER BY seq DESC))[1] AS head_hash,
        min(seq) FILTER (
            WHERE row_hash IS DISTINCT FROM expected
               OR (previous_seq IS NULL AND seq = 1 AND prev_hash IS NOT NULL)
               OR (previous_seq IS NOT NULL
                   AND (seq <> previous_seq + 1 OR prev_hash IS DISTINCT FROM previous_hash))
        ) AS first_broken_seq
    FROM chain
    """
)


async def check_audit_chain(db: AsyncSession, org_id: uuid.UUID) -> ChainCheck:
    """Recompute every stored event's hash and link, oldest first, in one query.

    The oldest stored event is trusted as the start (older ones may have left by retention);
    everything after it must hash to what is stored and point at the event before it.
    shortcut: one pass over the org's whole log; fine at thousands of events, page it by `seq`
    if a check ever takes seconds.
    """
    row = (await db.execute(_CHECK_CHAIN, {"org_id": org_id})).one()
    return ChainCheck(
        checked=int(row.checked),
        first_seq=row.first_seq,
        head_seq=row.head_seq,
        head_hash=row.head_hash,
        first_broken_seq=row.first_broken_seq,
    )
