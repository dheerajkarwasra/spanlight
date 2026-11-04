"""Reads of a project's telemetry losses.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.rollups.compute import floor_hour


@dataclass(frozen=True)
class LossRow:
    source: str
    reason: str
    count: int


_READ_LOSSES = text(
    """
    SELECT source, reason, sum(count) AS lost  -- numeric: a sum of bigints may exceed one
    FROM telemetry_losses_hourly
    WHERE project_id = :project_id AND bucket_start >= :first_hour AND bucket_start < :end
    GROUP BY source, reason
    ORDER BY source, reason
    """
)


async def read_losses(db: AsyncSession, project_id: uuid.UUID, window: TimeWindow) -> list[LossRow]:
    """The project's losses by source and reason, over every hour that overlaps `window`.

    Losses are kept per hour, so an hour that only partly overlaps the window counts whole.
    """
    rows = await db.execute(
        _READ_LOSSES,
        {"project_id": project_id, "first_hour": floor_hour(window.start), "end": window.end},
    )
    return [LossRow(source=row.source, reason=row.reason, count=int(row.lost)) for row in rows]


@dataclass(frozen=True)
class RefusedDay:
    day: date
    spans: int  # every refusal: the rate limit and both daily quotas
    rate_spans: int  # the rate limit alone, which clients usually resend within seconds


# The spans the ingest routes refused for the project's caps, by the UTC day of the loss hour.
_READ_QUOTA_REFUSALS = text(
    """
    SELECT CAST(bucket_start AT TIME ZONE 'UTC' AS date) AS day,
           sum(count) AS spans,
           sum(count) FILTER (WHERE reason = 'quota_rate') AS rate_spans
    FROM telemetry_losses_hourly
    WHERE project_id = :project_id AND source = 'ingest'
      AND reason IN ('quota_rate', 'quota_spans', 'quota_bytes')
      AND bucket_start >= :first_hour AND bucket_start < :end
    GROUP BY 1
    ORDER BY 1
    """
)


async def read_quota_refusals(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow
) -> list[RefusedDay]:
    """Spans refused by the project's ingest caps per UTC day, over the hours in `window`."""
    rows = await db.execute(
        _READ_QUOTA_REFUSALS,
        {"project_id": project_id, "first_hour": floor_hour(window.start), "end": window.end},
    )
    return [
        RefusedDay(day=row.day, spans=int(row.spans), rate_spans=int(row.rate_spans or 0))
        for row in rows
    ]
