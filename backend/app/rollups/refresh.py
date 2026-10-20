"""Pick the six-hour slices of the 48 hour refresh range that ``rollup_hourly`` rewrites next.

The job rewrites a project's last three hours on every run, and the rest of the last 48 hours
in slices of at most six hours (``app.rollups.jobs``). A slice is due when its oldest rollup row
was computed more than ``REFRESH_AGE`` ago, or when it has spans but no rollup row at all (a late
span arrived in hours that were empty when they were last rewritten). Due slices are returned
oldest first, missing ones before all others, so a run that runs out of budget leaves the
freshest slices for the next one.

Slices sit on a fixed six-hour UTC grid clipped to the range, so the same hours form the same
slice from one run to the next. The question spans projects, so it is read with RLS bypassed;
each slice is then rewritten in a transaction bound to its own project.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls

SLICE = timedelta(hours=6)
REFRESH_AGE = timedelta(hours=1)

Slice = tuple[uuid.UUID, datetime, datetime]

# One row per (project with spans in the range, slice): the oldest `computed_at` of the slice's
# span rollup rows (`span_rollups_hourly_project_bucket_idx`), and whether it has any span
# (`spans_project_started_window_idx`). Every table of an hour is rewritten in one transaction,
# so the span rollups speak for the trace and error rollups too.
_SLICE_STATE = text(
    """
    SELECT p.id AS project_id,
           w.start_at,
           w.end_at,
           (SELECT min(r.computed_at) FROM span_rollups_hourly AS r
            WHERE r.project_id = p.id
              AND r.bucket_start >= w.start_at AND r.bucket_start < w.end_at) AS oldest_computed,
           EXISTS (SELECT 1 FROM spans AS s
                   WHERE s.project_id = p.id
                     AND s.started_at >= w.start_at AND s.started_at < w.end_at) AS has_spans
    FROM projects AS p
    CROSS JOIN unnest(
        CAST(:starts AS timestamptz[]), CAST(:ends AS timestamptz[])
    ) AS w(start_at, end_at)
    WHERE EXISTS (
        SELECT 1 FROM spans AS s
        WHERE s.project_id = p.id AND s.started_at >= :start AND s.started_at < :end
    )
    """
).bindparams(
    bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
    bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def slice_bounds(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """``[start, end)`` cut on the six-hour UTC grid, oldest first. Both ends are whole hours."""
    first = start - (start - _EPOCH) % SLICE
    bounds = []
    current = first
    while current < end:
        bounds.append((max(current, start), min(current + SLICE, end)))
        current += SLICE
    return bounds


async def due_slices(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    start: datetime,
    end: datetime,
) -> list[Slice]:
    """The slices of ``[start, end)`` to rewrite, across every project with spans in it."""
    bounds = slice_bounds(start, end)
    if not bounds:
        return []
    async with session_factory() as db:
        await bypass_rls(db)
        rows = (
            await db.execute(
                _SLICE_STATE,
                {
                    "starts": [s for s, _ in bounds],
                    "ends": [e for _, e in bounds],
                    "start": start,
                    "end": end,
                },
            )
        ).all()
    stale_before = now - REFRESH_AGE
    due = [
        row
        for row in rows
        if (row.oldest_computed is None and row.has_spans)
        or (row.oldest_computed is not None and row.oldest_computed < stale_before)
    ]
    # Missing slices first, then the longest unrefreshed; the project and start break ties.
    due.sort(
        key=lambda row: (
            row.oldest_computed is not None,
            row.oldest_computed or now,
            row.project_id,
            row.start_at,
        )
    )
    return [(row.project_id, row.start_at, row.end_at) for row in due]
