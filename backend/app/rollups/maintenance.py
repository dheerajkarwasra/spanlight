"""One slice or one historical gap chunk for an already visited project."""

import uuid
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.rollups.compute import NEWEST_SPAN_BEFORE, ceil_hour, compute_rollups, floor_hour
from app.rollups.refresh import _find_gaps, due_slices


async def maintain_project(
    db: AsyncSession, project: uuid.UUID, now: datetime, kind: Literal["slice", "gap"]
) -> int:
    refresh_start = floor_hour(now - timedelta(hours=48))
    if kind == "slice":
        slices = await due_slices(
            db, project, now=now, start=refresh_start, end=floor_hour(now) - timedelta(hours=2)
        )
        if not slices:
            return 0
        _, start, end = slices[0]
    else:
        gaps = await _find_gaps(db, project, now, refresh_start)
        if not gaps:
            return 0
        _, _, start, end = gaps[0]
        newest = await db.scalar(
            NEWEST_SPAN_BEFORE, {"project_id": project, "start": start, "end": end}
        )
        if newest is None:
            return 0
        end = min(end, ceil_hour(newest + timedelta(microseconds=1)))
        start = max(start, end - timedelta(hours=24))
    return await compute_rollups(db, project, start, end)
