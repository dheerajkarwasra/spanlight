"""The audit log's filters, its event shape and its CSV and JSON Lines exports.

`GET /orgs/{id}/audit` and `GET /orgs/{id}/audit/export.{csv,jsonl}` take the same filters, so a
file holds what the filtered list shows. A JSON Lines row is the list's event object, which is
what log tools (a SIEM) ingest. Files are streamed in keyset batches, so a large log never sits in
memory.
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Query
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.api.schemas import AuditEventOut, UserOut
from app.core.errors import FieldError, ProblemError
from app.db.models import AuditEvent, User
from app.exports.schemas import as_utc
from app.exports.writers import csv_line
from app.services.audit import Outcome

AuditFileFormat = Literal["csv", "jsonl"]
ActorTypeFilter = Literal["user", "token", "system", "cli"]

# More matching events than this and an export is refused with `EXPORT_TOO_LARGE`.
MAX_AUDIT_EXPORT_ROWS = 50_000
AUDIT_BATCH_SIZE = 1000

AUDIT_CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "action",
    "actor_id",
    "actor_email",
    "actor_type",
    "credential_id",
    "project_id",
    "target_type",
    "target_id",
    "outcome",
    "reason",
    "ip",
    "user_agent",
    "request_id",
    "metadata",
)


@dataclass(frozen=True)
class AuditFilters:
    action: str | None = None
    actor_id: uuid.UUID | None = None
    from_: datetime | None = None
    to: datetime | None = None
    project_id: uuid.UUID | None = None
    outcome: Outcome | None = None
    actor_type: ActorTypeFilter | None = None

    def conditions(self, org_id: uuid.UUID) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [AuditEvent.org_id == org_id]
        if self.action is not None:
            conditions.append(AuditEvent.action == self.action)
        if self.actor_id is not None:
            conditions.append(AuditEvent.actor_user_id == self.actor_id)
        if self.from_ is not None:
            conditions.append(AuditEvent.created_at >= self.from_)
        if self.to is not None:
            conditions.append(AuditEvent.created_at < self.to)
        if self.project_id is not None:
            conditions.append(AuditEvent.project_id == self.project_id)
        if self.outcome is not None:
            conditions.append(AuditEvent.outcome == self.outcome)
        if self.actor_type is not None:
            conditions.append(AuditEvent.actor_type == self.actor_type)
        return conditions

    def as_metadata(self) -> dict[str, str]:
        """The filters that are set, for the `audit.export` event."""
        values = {
            "action": self.action,
            "actor_id": self.actor_id,
            "from": self.from_.isoformat() if self.from_ else None,
            "to": self.to.isoformat() if self.to else None,
            "project_id": self.project_id,
            "outcome": self.outcome,
            "actor_type": self.actor_type,
        }
        return {key: str(value) for key, value in values.items() if value is not None}


def audit_filters(
    action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    outcome: Annotated[Outcome | None, Query()] = None,
    actor_type: Annotated[ActorTypeFilter | None, Query()] = None,
) -> AuditFilters:
    """The `action`, `actor_id`, `from`, `to`, `project_id`, `outcome` and `actor_type` query
    parameters; `from` is inclusive, `to` not."""
    start = as_utc(from_) if from_ else None
    end = as_utc(to) if to else None
    if start is not None and end is not None and start >= end:
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "`from` must be earlier than `to`.",
            errors=[FieldError(field="from", message="must be earlier than `to`")],
        )
    return AuditFilters(
        action=action,
        actor_id=actor_id,
        from_=start,
        to=end,
        project_id=project_id,
        outcome=outcome,
        actor_type=actor_type,
    )


AuditQuery = Annotated[AuditFilters, Depends(audit_filters)]


def too_large() -> ProblemError:
    return ProblemError(
        422,
        "EXPORT_TOO_LARGE",
        f"More than {MAX_AUDIT_EXPORT_ROWS:,} audit events match. Narrow the export with "
        "`from`, `to`, `action` or `actor_id`.",
    )


async def count_events_up_to(
    db: AsyncSession, org_id: uuid.UUID, filters: AuditFilters, cap: int
) -> int:
    """How many events match, counting no further than `cap + 1`."""
    limited = select(AuditEvent.id).where(*filters.conditions(org_id)).limit(cap + 1)
    return int(await db.scalar(select(func.count()).select_from(limited.subquery())) or 0)


def _batch_query(
    org_id: uuid.UUID, filters: AuditFilters, after: tuple[datetime, uuid.UUID] | None
) -> Select[AuditEvent, User]:
    actor = aliased(User)
    query = (
        select(AuditEvent, actor)
        .outerjoin(actor, actor.id == AuditEvent.actor_user_id)
        .where(*filters.conditions(org_id))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(AUDIT_BATCH_SIZE)
    )
    if after is not None:
        created_at, event_id = after
        query = query.where(
            or_(
                AuditEvent.created_at < created_at,
                and_(AuditEvent.created_at == created_at, AuditEvent.id < event_id),
            )
        )
    return query


def audit_event_out(event: AuditEvent, actor: User | None) -> AuditEventOut:
    """One event as the API shows it. The actor's email falls back to the live user for events
    written before the email was recorded with them."""
    return AuditEventOut(
        id=event.id,
        action=event.action,
        actor=UserOut.model_validate(actor) if actor else None,
        actor_type=event.actor_type,
        actor_email=event.actor_email or (actor.email if actor else None),
        credential_id=event.credential_id,
        project_id=event.project_id,
        target_type=event.target_type,
        target_id=event.target_id,
        outcome=event.outcome,
        reason=event.reason,
        metadata=event.metadata_,
        ip=str(event.ip) if event.ip else None,
        user_agent=event.user_agent,
        request_id=event.request_id,
        created_at=event.created_at,
        seq=event.seq,
        prev_hash=event.prev_hash.hex() if event.prev_hash else None,
        hash=event.row_hash.hex() if event.row_hash else None,
    )


def _jsonl_line(event: AuditEvent, actor: User | None) -> bytes:
    return audit_event_out(event, actor).model_dump_json().encode() + b"\n"


def _event_line(event: AuditEvent, actor: User | None) -> bytes:
    return csv_line(
        (
            str(event.id),
            event.created_at.isoformat(),
            event.action,
            str(event.actor_user_id) if event.actor_user_id else "",
            event.actor_email or (actor.email if actor else ""),
            event.actor_type or "",
            str(event.credential_id) if event.credential_id else "",
            str(event.project_id) if event.project_id else "",
            event.target_type,
            event.target_id,
            event.outcome,
            event.reason or "",
            str(event.ip) if event.ip else "",
            event.user_agent or "",
            event.request_id or "",
            json.dumps(event.metadata_, ensure_ascii=False, separators=(",", ":")),
        )
    )


async def stream_audit_file(
    session_factory: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID,
    filters: AuditFilters,
    file_format: AuditFileFormat,
) -> AsyncIterator[bytes]:
    """The audit log as CSV or JSON Lines, newest first, one chunk per batch.

    It opens a session of its own: a response body is produced after the request's session
    dependency has finished, so the request's session cannot be relied on here. The caller has
    already authorized `org_id` and counted the rows.
    """
    line = _event_line if file_format == "csv" else _jsonl_line
    if file_format == "csv":
        yield csv_line(AUDIT_CSV_COLUMNS)
    after: tuple[datetime, uuid.UUID] | None = None
    while True:
        async with session_factory() as db:
            rows = (await db.execute(_batch_query(org_id, filters, after))).all()
        if not rows:
            return
        yield b"".join(line(event, actor) for event, actor in rows)
        if len(rows) < AUDIT_BATCH_SIZE:
            return
        last = rows[-1][0]
        after = (last.created_at, last.id)
