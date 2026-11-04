"""Atomic recent output and contiguous closed-hour settlement in the caller transaction."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.aggregate_job_progress import AggregateJobProgress
from app.rollups.compute import compute_rollups, floor_hour, lock_rollups
from app.rollups.coverage import complete_ranges, lock_aggregate_sources

KIND: Literal["rollup_recent"] = "rollup_recent"
HOUR = timedelta(hours=1)
CHUNK = timedelta(hours=3)
GRACE = timedelta(minutes=10)


@dataclass(frozen=True)
class Settlement:
    cursor: datetime
    pending_since: datetime | None
    rows: int = 0
    refreshed_hours: int = 0
    unavailable_hours: int = 0
    stale: bool = False


def closed_target(now: datetime) -> datetime:
    return floor_hour(now - GRACE)


async def locked_progress(db: AsyncSession, project: uuid.UUID) -> AggregateJobProgress:
    row = await db.scalar(
        select(AggregateJobProgress)
        .where(AggregateJobProgress.project_id == project, AggregateJobProgress.kind == KIND)
        .with_for_update()
    )
    if row is None or row.admitted_hour is None or row.next_closed_hour is None:
        raise RuntimeError("Required recent aggregate admission is missing")
    return row


def _ranges(
    cursor: datetime, end: datetime, now: datetime, recent: bool
) -> list[tuple[datetime, datetime]]:
    ranges = [(cursor, end)] if cursor < end else []
    if recent:
        ranges.append((floor_hour(now) - 2 * HOUR, floor_hour(now) + HOUR))
    ranges.sort()
    if len(ranges) == 2 and ranges[0][1] >= ranges[1][0]:
        return [(ranges[0][0], max(ranges[0][1], ranges[1][1]))]
    return ranges


async def settle_recent(
    db: AsyncSession,
    project: uuid.UUID,
    now: datetime,
    *,
    recent: bool = True,
    expected: datetime | None = None,
    until: datetime | None = None,
) -> Settlement | None:
    """Examine at most three closed hours; preserve all unavailable snapshots and dirty bits.

    A missing parent is a deletion, while missing admission on a live parent is an error.
    Source serialization protects both the coverage decision and every output/cursor write.
    """
    if not await lock_aggregate_sources(db, project):
        return None
    await lock_rollups(db, project)
    row = await locked_progress(db, project)
    cursor = row.next_closed_hour
    if cursor is None:
        raise RuntimeError("Required recent aggregate cursor is missing")
    target = closed_target(now)
    if expected is not None and cursor != expected:
        return Settlement(cursor, row.closed_pending_since, stale=True)
    end = min(cursor + CHUNK, target, until or target)
    rows = refreshed = unavailable = 0
    for start, stop in _ranges(cursor, end, now, recent):
        safe = await complete_ranges(db, project, start, stop)
        complete_hours = sum(int((b - a) / HOUR) for a, b in safe)
        refreshed += complete_hours
        unavailable += int((stop - start) / HOUR) - complete_hours
        for first, last in safe:
            rows += await compute_rollups(db, project, first, last)
    # Coverage classified every hour in this bounded prefix, including complete empty hours.
    row.next_closed_hour = max(cursor, end)
    if row.next_closed_hour >= target:
        row.closed_pending_since = None
    elif row.closed_pending_since is None:
        row.closed_pending_since = cursor + HOUR + GRACE
    await db.flush()
    return Settlement(row.next_closed_hour, row.closed_pending_since, rows, refreshed, unavailable)
