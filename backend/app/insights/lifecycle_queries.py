"""The SQL behind the insight lifecycle (`app.insights.service`).

`insights` is under row-level security, so every statement here runs in a transaction bound to
the project (or with the worker's bypass); the explicit `project_id` filters are a second guard.
Rows are locked and written in fingerprint order, so two transactions applying overlapping
batches wait for each other instead of deadlocking.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    and_,
    case,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Insight
from app.insights.schemas import InsightStatus

# An open or acknowledged insight not detected again for this long is resolved.
QUIET_PERIOD = timedelta(hours=24)

_STATUS_TYPE = Insight.__table__.c.status.type


@dataclass(frozen=True, slots=True)
class UpsertedRow:
    """One row the upsert wrote: its status afterwards, and whether it was a new row."""

    id: uuid.UUID
    fingerprint: str
    status: InsightStatus
    inserted: bool


async def lock_by_fingerprint(
    db: AsyncSession, project_id: uuid.UUID, fingerprints: Sequence[str]
) -> dict[str, InsightStatus]:
    """The current status of the insights with these fingerprints, locked until commit.

    A fingerprint with no insight yet is absent from the result.
    """
    if not fingerprints:
        return {}
    rows = await db.execute(
        select(Insight.fingerprint, Insight.status)
        .where(Insight.project_id == project_id, Insight.fingerprint.in_(fingerprints))
        .order_by(Insight.fingerprint)
        .with_for_update()
    )
    return {fingerprint: status for fingerprint, status in rows}


def _active_mute(now: datetime) -> ColumnElement[bool]:
    return and_(Insight.status == InsightStatus.MUTED, Insight.muted_until > now)


def _status_after_detection(now: datetime) -> ColumnElement[Any]:
    """Open and acknowledged stay as they are, a running mute holds, anything else reopens."""
    keeps = or_(
        _active_mute(now),
        Insight.status.in_([InsightStatus.OPEN, InsightStatus.ACKNOWLEDGED]),
    )
    return case((keeps, Insight.status), else_=literal(InsightStatus.OPEN, _STATUS_TYPE))


def _detection_updates(excluded: Any, now: datetime) -> dict[str, Any]:
    """The SET of a re-detection: the measured values replaced, the lifecycle per the table in
    `app.insights.service`."""
    keeps_mute = _active_mute(now)
    keeps_acknowledgement = or_(keeps_mute, Insight.status == InsightStatus.ACKNOWLEDGED)
    return {
        "status": _status_after_detection(now),
        "severity": excluded.severity,
        "title": excluded.title,
        "summary": excluded.summary,
        "failure_layer": excluded.failure_layer,
        "certainty": excluded.certainty,
        "evidence": excluded.evidence,
        "suggested_fix": excluded.suggested_fix,
        "verification": excluded.verification,
        "last_seen_at": excluded.last_seen_at,
        "occurrences": Insight.occurrences + 1,
        # GREATEST ignores NULLs: a detection without a reading keeps the stored peak.
        "peak_strength": func.greatest(Insight.peak_strength, excluded.peak_strength),
        "resolved_at": null(),
        "muted_until": case((keeps_mute, Insight.muted_until), else_=null()),
        "mute_reason": case((keeps_mute, Insight.mute_reason), else_=null()),
        "acknowledged_by": case((keeps_acknowledgement, Insight.acknowledged_by), else_=null()),
        "acknowledged_at": case((keeps_acknowledgement, Insight.acknowledged_at), else_=null()),
    }


async def upsert_detections(
    db: AsyncSession, rows: Sequence[Mapping[str, Any]], now: datetime
) -> list[UpsertedRow]:
    """Insert new insights and update re-detected ones, in one statement.

    `rows` are full `insights` rows for a first detection (status `open`, `occurrences` 1,
    both seen times `now`), one per fingerprint. On a conflict the stored row keeps its id,
    `first_seen_at` and, unless it reopens, its status; everything the detection measured
    (severity, evidence, copy, `last_seen_at`) is replaced, `occurrences` goes up by one and
    `peak_strength` keeps the larger reading.
    A reopened insight loses its mute and its acknowledgement.
    """
    if not rows:
        return []
    statement = insert(Insight).values(sorted(rows, key=lambda row: str(row["fingerprint"])))
    upsert = statement.on_conflict_do_update(
        index_elements=[Insight.project_id, Insight.fingerprint],
        set_=_detection_updates(statement.excluded, now),
    ).returning(
        Insight.id,
        Insight.fingerprint,
        Insight.status,
        # xmax is 0 on a row this statement inserted and set on one it updated.
        literal_column("(xmax = 0)", Boolean).label("inserted"),
    )
    result = await db.execute(upsert)
    return [
        UpsertedRow(
            id=row.id, fingerprint=row.fingerprint, status=row.status, inserted=row.inserted
        )
        for row in result
    ]


async def resolve_quiet(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: str,
    detected: Sequence[str],
    now: datetime,
) -> int:
    """Resolve the kind's insights that were not detected for `QUIET_PERIOD`.

    Open and acknowledged insights resolve, and so do muted ones whose mute has ended; a running
    mute holds. `detected` are the fingerprints this run found, which never resolve. Returns how
    many resolved.
    """
    quiet = and_(
        Insight.project_id == project_id,
        Insight.kind == kind,
        Insight.last_seen_at < now - QUIET_PERIOD,
        or_(
            Insight.status.in_([InsightStatus.OPEN, InsightStatus.ACKNOWLEDGED]),
            and_(Insight.status == InsightStatus.MUTED, Insight.muted_until <= now),
        ),
    )
    if detected:
        quiet = and_(quiet, Insight.fingerprint.not_in(detected))
    result = await db.execute(
        update(Insight)
        .where(quiet)
        .values(status=InsightStatus.RESOLVED, resolved_at=now, muted_until=None, mute_reason=None)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))


async def lock_insight(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID
) -> Insight | None:
    """The insight, locked until the transaction ends, so two actions do not interleave."""
    return await db.scalar(
        select(Insight)
        .where(Insight.project_id == project_id, Insight.id == insight_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
