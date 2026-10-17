"""Reads of a project's telemetry losses.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid
from dataclasses import dataclass

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
