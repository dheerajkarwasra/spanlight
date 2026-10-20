"""The ``rollup_hourly`` job: keep the hourly rollups current.

Every run does three things, in this order:

1. It recomputes the last three hours, ``[floor_hour(now) - 2 h, floor_hour(now) + 1 h)``, for
   each project that has spans in them, in one transaction per project. A span that arrives
   late into those hours, or is sent again with other attributes, is reflected within one run.
2. It refreshes the rest of the last 48 hours in slices of at most six hours, one transaction
   per slice, oldest first, while the budget lasts (``app.rollups.refresh``). A slice is due
   about once an hour, so a span that arrives later than the recent hours is reflected within
   about an hour. Rewriting all 48 hours every run took 30-87 s for a project with 2 M spans a
   day, past the task timeout; three hours or one slice take a few seconds.
3. It backfills history. A project whose oldest span inside its retention (capped at 90 days)
   is older than its oldest rollup hour has a gap, and the job fills ``[oldest span hour,
   oldest rollup hour)`` in daily chunks, newest first. Because the gap is read from the data,
   a backfill that was cut short (the task has a hard timeout) simply continues on the next run.

   Error rollups get their own gap, because they are sparse: only hours with a failed llm call
   have a row, so their oldest row says nothing about the span rollups and the oldest span says
   nothing about them. Their gap runs from the hour of the oldest span that makes an error
   rollup row (``FAILED_LLM_SPAN``, the insert's own predicate) to the oldest error rollup hour.
   A project that has span rollups but no error history (an install upgraded with data) is
   backfilled that way, and a history without failures never opens a gap. The two gaps of a
   project are merged into one range, since a backfill rebuilds every table of the hours it
   covers.

The recompute comes first so a slow refresh or backfill never delays current data, and both stop
starting new slices or chunks after ``BACKFILL_BUDGET_SECONDS`` so the run ends inside the task
timeout.
Projects with a gap are served smallest gap first: a small project is finished in one run and
a large one cannot starve the others, while the large one still advances with every run
because the budget it gets is whatever the smaller ones left over.

Spans older than 48 hours are not revisited except at the old end of that gap. A span that
arrives later than that, in the middle of the history, is missing from the rollups until
someone runs ``spanlight rollups backfill`` for its hours.

Invariant the metrics reads rely on: every hour more than three hours old was last rewritten
after it closed (at the latest when it was the oldest of the recent hours, about two hours after
it closed), and the recent hours of a project share the ``computed_at`` of its last run. So the
``computed_at`` of a project's newest rollup row is a watermark: every whole hour that closed at
least ten minutes before it is complete as of that run. Keep both when changing this job.

After the rollups, the run also heals ``traces.has_scores`` flags a score/ingest race left unset
(``app.scores.repair``); it shares this job rather than adding a job kind for a small sweep.
"""

import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import ROLLUP_DURATION
from app.db.rls import bypass_rls
from app.ingest.error_class import ErrorClass
from app.jobs.context import TaskContext
from app.rollups.compute import backfill_rollups, compute_rollups, floor_hour
from app.rollups.error_compute import FAILED_LLM_SPAN
from app.rollups.refresh import due_slices
from app.scores.repair import repair_has_scores

logger = structlog.get_logger(__name__)

# The recent hours rewritten on every run: the current hour and the two before it.
RECOMPUTE_WINDOW = timedelta(hours=2)
# The range kept current by the refresh slices; older hours are only backfilled.
REFRESH_WINDOW = timedelta(hours=48)
MAX_BACKFILL_DAYS = 90
# The worker kills a task after 50 s; stopping at 30 s leaves room for the chunk in flight.
BACKFILL_BUDGET_SECONDS = 30.0

# Projects with spans in the recompute window. The project list is read with RLS bypassed because
# the question spans projects; every write afterwards is bound to a single project.
_PROJECTS_WITH_RECENT_SPANS = text(
    """
    SELECT p.id AS project_id
    FROM projects AS p
    WHERE EXISTS (
        SELECT 1 FROM spans AS s
        WHERE s.project_id = p.id AND s.started_at >= :window_start AND s.started_at < :end
    )
    ORDER BY p.id
    """
)

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
    WHERE oldest.started_at IS NOT NULL
    """
)
_ERROR_CLASSES = [error_class.value for error_class in ErrorClass]

Gap = tuple[timedelta, uuid.UUID, datetime, datetime]


async def run_rollup_hourly(context: TaskContext, _: dict[str, Any]) -> None:
    started = time.perf_counter()
    now = datetime.now(UTC)
    try:
        projects, backfilled = await roll_up_all(context.session_factory, now=now, context=context)
    finally:
        ROLLUP_DURATION.observe(time.perf_counter() - started)
    logger.info("rollups_recomputed", projects=projects, backfilled_rows=backfilled)
    # Piggybacks on this five-minute job: a small sweep, and a missed run is caught by the next.
    # Its failure is logged, never raised: the rollups above are committed and must not be
    # reported as a failed job.
    try:
        async with context.session_factory() as db:
            repaired = await repair_has_scores(db, now)
            await db.commit()
    except Exception as exc:
        logger.error("has_scores_repair_failed", error_type=type(exc).__name__, exc_info=exc)
        return
    if repaired:
        logger.info("has_scores_repaired", traces=repaired)


async def roll_up_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    context: TaskContext | None = None,
) -> tuple[int, int]:
    """Recompute the recent hours, refresh the due slices of the last 48 hours, then backfill gaps.

    Returns the number of projects recomputed and the number of rows the backfill wrote.
    """
    deadline = time.monotonic() + BACKFILL_BUDGET_SECONDS
    window_start = floor_hour(now) - RECOMPUTE_WINDOW
    # `end` is exclusive and hour-aligned upwards, so the hour that holds `now` is included.
    window_end = floor_hour(now) + timedelta(hours=1)
    refresh_start = floor_hour(now - REFRESH_WINDOW)

    async with session_factory() as db:
        await bypass_rls(db)
        recent = (
            await db.execute(
                _PROJECTS_WITH_RECENT_SPANS, {"window_start": window_start, "end": window_end}
            )
        ).all()

    for row in recent:
        await _recompute(session_factory, context, row.project_id, window_start, window_end)

    slices = await due_slices(session_factory, now=now, start=refresh_start, end=window_start)
    refreshed = 0
    for project_id, slice_start, slice_end in slices:
        if time.monotonic() >= deadline:
            break
        await _recompute(session_factory, context, project_id, slice_start, slice_end)
        refreshed += 1
    if slices:
        logger.info("rollup_slices_refreshed", refreshed=refreshed, due=len(slices))

    gaps = await _find_gaps(session_factory, now, refresh_start)

    backfilled = 0
    for _, project_id, gap_start, gap_end in gaps:
        if time.monotonic() >= deadline:
            break
        async with session_factory() as db:
            backfilled += await backfill_rollups(
                db,
                project_id,
                gap_start,
                gap_end,
                before_chunk=context.heartbeat if context else None,
                deadline=deadline,
            )
    return len(recent), backfilled


async def _find_gaps(
    session_factory: async_sessionmaker[AsyncSession], now: datetime, window_start: datetime
) -> list[Gap]:
    async with session_factory() as db:
        await bypass_rls(db)
        candidates = (
            await db.execute(
                _BACKFILL_CANDIDATES,
                {
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


async def _recompute(
    session_factory: async_sessionmaker[AsyncSession],
    context: TaskContext | None,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> None:
    if context is not None:
        await context.heartbeat()
    async with session_factory() as db:
        await compute_rollups(db, project_id, start, end)
        await db.commit()
