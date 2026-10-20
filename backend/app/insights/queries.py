"""The reads behind the insights API: the list, one insight, the badge counts, detector runs.

`insights` and `detector_runs` are under row-level security, so the caller's transaction is bound
to the project; the explicit `project_id` filters are a second guard.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import cast, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetectorRun, Insight
from app.insights.schemas import InsightStatus, Severity

# The statuses that still need a person; what the nav badge counts.
ACTIVE_STATUSES = (InsightStatus.OPEN, InsightStatus.ACKNOWLEDGED)


async def list_insights(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    statuses: Sequence[InsightStatus] | None,
    severity: Severity | None,
    kind: str | None,
    trace_id: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Insight]:
    """Most recently seen first; one row more than `limit` tells the caller there is a next page."""
    query = select(Insight).where(Insight.project_id == project_id)
    if statuses:
        query = query.where(Insight.status.in_(statuses))
    if severity is not None:
        query = query.where(Insight.severity == severity)
    if kind is not None:
        query = query.where(Insight.kind == kind)
    if trace_id is not None:
        # Runs on the project's insights: under row-level security `@>` (not leakproof) cannot
        # use an index, so there is none on `evidence` (migration 1009).
        trace_ids = Insight.evidence.op("->")(text("'trace_ids'"))
        query = query.where(trace_ids.op("@>")(cast(func.json_build_array(trace_id), JSONB)))
    if after is not None:
        query = query.where(tuple_(Insight.last_seen_at, Insight.id) < tuple_(*after))
    query = query.order_by(Insight.last_seen_at.desc(), Insight.id.desc()).limit(limit + 1)
    return list((await db.scalars(query)).all())


async def get_insight(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID
) -> Insight | None:
    return await db.scalar(
        select(Insight).where(Insight.project_id == project_id, Insight.id == insight_id)
    )


async def count_active_by_severity(db: AsyncSession, project_id: uuid.UUID) -> dict[Severity, int]:
    rows = await db.execute(
        select(Insight.severity, func.count())
        .where(Insight.project_id == project_id, Insight.status.in_(ACTIVE_STATUSES))
        .group_by(Insight.severity)
    )
    return {severity: count for severity, count in rows.all()}


async def list_detector_runs(
    db: AsyncSession, project_id: uuid.UUID, limit: int
) -> list[DetectorRun]:
    return list(
        (
            await db.scalars(
                select(DetectorRun)
                .where(DetectorRun.project_id == project_id)
                .order_by(DetectorRun.ran_at.desc(), DetectorRun.id.desc())
                .limit(limit)
            )
        ).all()
    )
