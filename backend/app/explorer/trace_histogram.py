"""Trace and error counts over time, for exactly the traces the trace list shows."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.traces import TraceBucketOut, TraceHistogramOut
from app.api.trace_filters import TraceListFilters
from app.api.window import TimeWindow
from app.db.models import Trace

HOURLY_UP_TO = timedelta(hours=48)
# Any UTC midnight: bins of whole hours or days from it start where `date_trunc` in UTC would.
UTC_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)


async def trace_histogram(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, filters: TraceListFilters
) -> TraceHistogramOut:
    """Hour buckets for a window of 48 h or less, else days, truncated in UTC. Buckets without a
    trace are left out; `errors` counts traces with at least one failed span."""
    hourly = window.length <= HOURLY_UP_TO
    # date_bin is arithmetic on the instant; date_trunc in a time zone took about 1.5 times as long.
    bucket_start = func.date_bin(
        timedelta(hours=1) if hourly else timedelta(days=1),
        Trace.started_at,
        UTC_ORIGIN,
        type_=DateTime(timezone=True),
    )
    query = select(
        bucket_start.label("bucket_start"),
        func.count().label("traces"),
        func.count().filter(Trace.error_count > 0).label("errors"),
    )
    query = await filters.narrow(db, query, project_id, window)
    query = query.group_by(bucket_start).order_by(bucket_start)
    with filters.too_broad_on_timeout():
        rows = (await db.execute(query)).all()
    return TraceHistogramOut(
        bucket="hour" if hourly else "day",
        buckets=[TraceBucketOut(bucket_start=start, traces=n, errors=e) for start, n, e in rows],
    )
