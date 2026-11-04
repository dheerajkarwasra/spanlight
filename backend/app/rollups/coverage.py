"""Serialize source loss and preserve aggregates whose raw history is incomplete."""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bind_project

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


async def lock_aggregate_sources(db: AsyncSession, project_id: uuid.UUID) -> bool:
    """Take the parent before the shared transaction lock; caller retains it through commit."""
    await bind_project(db, project_id)
    found = await db.scalar(
        text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE"),
        {"project_id": project_id},
    )
    if found is None:
        return False
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"aggregate-source-loss:{project_id}"},
    )
    return True


async def complete_ranges(
    db: AsyncSession, project_id: uuid.UUID, start: datetime, end: datetime, *, daily: bool = False
) -> list[tuple[datetime, datetime]]:
    """Complete contiguous buckets, checked after the caller takes the source-loss lock."""
    step = DAY if daily else HOUR
    rows = await db.scalars(
        text("""
        SELECT bucket FROM generate_series(
            CAST(:start AS timestamptz), CAST(:end AS timestamptz) - CAST(:step AS interval),
            CAST(:step AS interval)
        ) bucket
        JOIN projects p ON p.id = :project_id
        WHERE bucket >= p.aggregate_history_unverified_before
          AND NOT EXISTS (
              SELECT 1 FROM aggregate_incomplete_hours h
              WHERE h.project_id = :project_id AND h.bucket_hour >= bucket
                AND h.bucket_hour < bucket + CAST(:step AS interval)
          )
        ORDER BY bucket
    """),
        {"project_id": project_id, "start": start, "end": end, "step": step},
    )
    ranges: list[tuple[datetime, datetime]] = []
    for bucket in rows:
        if ranges and ranges[-1][1] == bucket:
            ranges[-1] = (ranges[-1][0], bucket + step)
        else:
            ranges.append((bucket, bucket + step))
    return ranges


async def complete_day_ranges(
    db: AsyncSession, project_id: uuid.UUID, days: Sequence[date]
) -> list[tuple[date, date]]:
    """Inclusive safe stretches within the existing min/max daily refresh contract."""
    if not days:
        return []
    start = datetime.combine(min(days), datetime.min.time(), UTC)
    end = datetime.combine(max(days) + DAY, datetime.min.time(), UTC)
    return [
        (first.date(), (last - DAY).date())
        for first, last in await complete_ranges(db, project_id, start, end, daily=True)
    ]


async def mark_trace_source_loss(
    db: AsyncSession,
    project_id: uuid.UUID,
    trace_ids: Sequence[str],
    reason: Literal["retention", "erasure"],
) -> None:
    """Lock the exact source set before reading hours in a fresh statement snapshot."""
    if not trace_ids:
        return
    params = {"project_id": project_id, "trace_ids": list(trace_ids), "reason": reason}
    await db.execute(
        text("""
        SELECT trace_id FROM traces
        WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
        ORDER BY trace_id FOR UPDATE
    """),
        params,
    )
    await db.execute(
        text("""
        INSERT INTO aggregate_incomplete_hours (project_id, bucket_hour, reason)
        SELECT :project_id, hour, :reason FROM (
            SELECT date_trunc('hour', started_at, 'UTC') AS hour FROM traces
            WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
            UNION
            SELECT date_trunc('hour', started_at, 'UTC') FROM spans
            WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
        ) lost
        ON CONFLICT (project_id, bucket_hour) DO NOTHING
    """),
        params,
    )
