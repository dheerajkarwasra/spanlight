"""One project-scoped six-hour maintenance slice, oldest computation first."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Row, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.error_class import ErrorClass
from app.rollups.compute import floor_hour
from app.rollups.error_compute import FAILED_LLM_SPAN

MAX_BACKFILL_DAYS = 90

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
    WHERE p.id = :project_id AND EXISTS (
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
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    now: datetime,
    start: datetime,
    end: datetime,
) -> list[Slice]:
    """At most one due slice; the caller owns the bound session and total deadline."""
    bounds = slice_bounds(start, end)
    if not bounds:
        return []
    statement = text(f"""
        SELECT * FROM ({_SLICE_STATE.text}) slices
        WHERE (oldest_computed IS NULL AND has_spans) OR oldest_computed < :stale_before
        ORDER BY oldest_computed NULLS FIRST, start_at LIMIT 1
    """).bindparams(
        bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
    )
    rows = (
        await db.execute(
            statement,
            {
                "project_id": project_id,
                "starts": [s for s, _ in bounds],
                "ends": [e for _, e in bounds],
                "start": start,
                "end": end,
                "stale_before": now - REFRESH_AGE,
            },
        )
    ).all()
    return [(row.project_id, row.start_at, row.end_at) for row in rows]


# For each project with a span inside its retention (capped) and older than the recompute window:
# that oldest span and the oldest span rollup hour, then the oldest such span that makes an error
# rollup row and the oldest error rollup hour. All are index lookups; the oldest failure is one
# probe per error class on `spans_project_error_class_started_idx`, so a project with many
# failures is not scanned.
_BACKFILL_CANDIDATES = text(
    f"""
    SELECT p.id AS project_id,
           oldest.started_at AS oldest_span,
           (SELECT min(r.bucket_start) FROM span_rollups_hourly AS r
            WHERE r.project_id = p.id) AS oldest_rollup,
           failure.started_at AS oldest_failure,
           (SELECT min(e.bucket_start) FROM error_rollups_hourly AS e
            WHERE e.project_id = p.id) AS oldest_error_rollup
    FROM projects AS p
    CROSS JOIN LATERAL (
        SELECT :now - make_interval(days => least(p.retention_days, :max_days)) AS since
    ) AS kept
    CROSS JOIN LATERAL (
        SELECT min(s.started_at) AS started_at
        FROM spans AS s
        WHERE s.project_id = p.id
          AND s.started_at >= kept.since
          AND s.started_at < :window_start
    ) AS oldest
    CROSS JOIN LATERAL (
        SELECT min(first.started_at) AS started_at
        FROM unnest(CAST(:error_classes AS text[])) AS c(error_class)
        CROSS JOIN LATERAL (
            SELECT s.started_at
            FROM spans AS s
            WHERE s.project_id = p.id
              AND s.error_class = c.error_class
              AND s.started_at >= kept.since
              AND s.started_at < :window_start
              AND {FAILED_LLM_SPAN}
            ORDER BY s.started_at
            LIMIT 1
        ) AS first
    ) AS failure
    WHERE p.id = :project_id AND oldest.started_at IS NOT NULL
    """
)
_ERROR_CLASSES = [error_class.value for error_class in ErrorClass]

Gap = tuple[timedelta, uuid.UUID, datetime, datetime]


async def _find_gaps(
    db: AsyncSession, project_id: uuid.UUID, now: datetime, window_start: datetime
) -> list[Gap]:
    candidates = (
        await db.execute(
            _BACKFILL_CANDIDATES,
            {
                "project_id": project_id,
                "now": now,
                "max_days": MAX_BACKFILL_DAYS,
                "window_start": window_start,
                "error_classes": _ERROR_CLASSES,
            },
        )
    ).all()
    return _gaps(candidates, window_start)


def _gaps(candidates: Sequence[Row[Any]], window_start: datetime) -> list[Gap]:
    """Each project's backfill range, smallest first (the project id breaks ties).

    A project's range covers both of its gaps: the span gap and the error gap (module docstring).
    Recent rollups were just written, so the oldest rollup is normally at or after
    `window_start`; each gap ends where its rollups begin, and never after `window_start`.
    """
    gaps: list[Gap] = []
    for row in candidates:
        ranges = [_gap(row.oldest_span, row.oldest_rollup, window_start)]
        if row.oldest_failure is not None:
            ranges.append(_gap(row.oldest_failure, row.oldest_error_rollup, window_start))
        open_ranges = [(start, end) for start, end in ranges if start < end]
        if open_ranges:
            gap_start = min(start for start, _ in open_ranges)
            gap_end = max(end for _, end in open_ranges)
            gaps.append((gap_end - gap_start, row.project_id, gap_start, gap_end))
    gaps.sort(key=lambda gap: (gap[0], gap[1]))
    return gaps


def _gap(
    oldest: datetime, oldest_rollup: datetime | None, window_start: datetime
) -> tuple[datetime, datetime]:
    end = window_start if oldest_rollup is None else min(oldest_rollup, window_start)
    return floor_hour(oldest), end
