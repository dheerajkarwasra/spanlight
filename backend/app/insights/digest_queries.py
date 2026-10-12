"""Reads of the weekly digest's Insights section. The transaction must be bound to the project."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Insight
from app.insights.schemas import InsightStatus, Severity

# "Open" in the digest, as in the health score: not resolved and not muted.
OPEN_STATUSES = (InsightStatus.OPEN, InsightStatus.ACKNOWLEDGED)


@dataclass(frozen=True, slots=True)
class OpenInsightRow:
    kind: str
    severity: Severity
    title: str


async def open_counts(db: AsyncSession, project_id: uuid.UUID) -> dict[Severity, int]:
    """How many insights are open or acknowledged, per severity; a severity with none is
    absent."""
    rows = await db.execute(
        select(Insight.severity, func.count())
        .where(Insight.project_id == project_id, Insight.status.in_(OPEN_STATUSES))
        .group_by(Insight.severity)
    )
    return {Severity(severity): int(count) for severity, count in rows}


async def top_open(db: AsyncSession, project_id: uuid.UUID, limit: int) -> list[OpenInsightRow]:
    """The open or acknowledged insights to list: critical first, then warning, then info; the
    most recently seen first within a severity.

    The enum sorts in declaration order (info < warning < critical), so descending puts critical
    first.
    """
    rows = await db.execute(
        select(Insight.kind, Insight.severity, Insight.title)
        .where(Insight.project_id == project_id, Insight.status.in_(OPEN_STATUSES))
        .order_by(Insight.severity.desc(), Insight.last_seen_at.desc(), Insight.id.desc())
        .limit(limit)
    )
    return [
        OpenInsightRow(kind=kind, severity=Severity(severity), title=title)
        for kind, severity, title in rows
    ]


async def any_first_seen(
    db: AsyncSession, project_id: uuid.UUID, start: datetime, end: datetime
) -> bool:
    """Whether an insight was first seen in `[start, end)`, whatever its status now."""
    found = await db.scalar(
        select(Insight.id)
        .where(
            Insight.project_id == project_id,
            Insight.first_seen_at >= start,
            Insight.first_seen_at < end,
        )
        .limit(1)
    )
    return found is not None
