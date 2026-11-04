"""Bound the hourly/raw split without certifying unfinished closed-hour work."""

import uuid
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rollups.compute import floor_hour

# Current rows alone cannot certify an admitted closed-hour backlog.
_WATERMARK = text(
    """
    SELECT g.next_closed_hour, (
        SELECT computed_at FROM span_rollups_hourly
        WHERE project_id = :project_id ORDER BY bucket_start DESC LIMIT 1
    ) AS computed_at
    FROM aggregate_job_progress g
    WHERE g.project_id = :project_id AND g.kind = 'rollup_recent'
    """
)

ROLLUP_GRACE = timedelta(minutes=10)
"""How long after an hour closes its spans may still arrive before the hour counts as complete."""

RAW_TAIL_MAX = timedelta(hours=24)
"""The longest raw tail a read accepts: rollups more than a day behind are read as they are."""


async def read_split(db: AsyncSession, project_id: uuid.UUID, now: datetime) -> datetime | None:
    """Cap the materialized frontier by closed-hour settlement, retaining the raw-tail bound.

    When the bound forces materialization beyond the cursor, read_covered reports pending
    for the consumed unfinished interval. A project without rollup rows still reads raw.
    """
    row = (await db.execute(_WATERMARK, {"project_id": project_id})).one_or_none()
    if row is None:
        raise RuntimeError("Required recent aggregate admission is missing")
    if row.computed_at is None:
        return None
    frontier = min(floor_hour(row.computed_at - ROLLUP_GRACE), row.next_closed_hour)
    return cast(datetime, max(frontier, floor_hour(now - RAW_TAIL_MAX)))
