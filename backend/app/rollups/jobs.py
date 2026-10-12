"""The ``rollup_hourly`` job: keep the hourly rollups current.

Every run does two things, in this order:

1. It recomputes the last 48 hours for each project that has spans in that window, so a span
   that arrives late, or is sent again with other attributes, is reflected within one run.
2. It backfills history. A project whose oldest span inside its retention (capped at 90 days)
   is older than its oldest rollup hour has a gap, and the job fills ``[oldest span hour,
   oldest rollup hour)`` in daily chunks, newest first. Because the gap is read from the data,
   a backfill that was cut short (the task has a hard timeout) simply continues on the next run.

The recompute comes first so a slow backfill never delays current data, and the backfill stops
starting new chunks after ``BACKFILL_BUDGET_SECONDS`` so the run ends inside the task timeout.
Projects with a gap are served smallest gap first: a small project is finished in one run and
a large one cannot starve the others, while the large one still advances with every run
because the budget it gets is whatever the smaller ones left over.

Spans older than 48 hours are not revisited except at the old end of that gap. A span that
arrives later than that, in the middle of the history, is missing from the rollups until
someone runs ``spanlight rollups backfill`` for its hours.

After the rollups, the run also heals ``traces.has_scores`` flags a score/ingest race left unset
(``app.scores.repair``); it shares this job rather than adding a job kind for a small sweep.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import ROLLUP_DURATION
from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.rollups.compute import backfill_rollups, compute_rollups, floor_hour
from app.scores.repair import repair_has_scores

logger = structlog.get_logger(__name__)

RECOMPUTE_WINDOW = timedelta(hours=48)
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

# For each project, its oldest span that is inside its retention (capped) and older than the
# recompute window, and its oldest span rollup hour. Both are index lookups.
_BACKFILL_CANDIDATES = text(
    """
    SELECT p.id AS project_id,
           oldest.started_at AS oldest_span,
           (SELECT min(r.bucket_start) FROM span_rollups_hourly AS r
            WHERE r.project_id = p.id) AS oldest_rollup
    FROM projects AS p
    CROSS JOIN LATERAL (
        SELECT min(s.started_at) AS started_at
        FROM spans AS s
        WHERE s.project_id = p.id
          AND s.started_at >= :now - make_interval(days => least(p.retention_days, :max_days))
          AND s.started_at < :window_start
    ) AS oldest
    WHERE oldest.started_at IS NOT NULL
    """
)


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
    """Recompute the recent window, then backfill gaps.

    Returns the number of projects recomputed and the number of rows the backfill wrote.
    """
    deadline = time.monotonic() + BACKFILL_BUDGET_SECONDS
    window_start = floor_hour(now - RECOMPUTE_WINDOW)
    # `end` is exclusive and hour-aligned upwards, so the hour that holds `now` is included.
    window_end = floor_hour(now) + timedelta(hours=1)

    async with session_factory() as db:
        await bypass_rls(db)
        recent = (
            await db.execute(
                _PROJECTS_WITH_RECENT_SPANS, {"window_start": window_start, "end": window_end}
            )
        ).all()

    for row in recent:
        await _recompute(session_factory, context, row.project_id, window_start, window_end)

    async with session_factory() as db:
        await bypass_rls(db)
        candidates = (
            await db.execute(
                _BACKFILL_CANDIDATES,
                {"now": now, "max_days": MAX_BACKFILL_DAYS, "window_start": window_start},
            )
        ).all()

    gaps: list[tuple[timedelta, uuid.UUID, datetime, datetime]] = []
    for row in candidates:
        gap_start = floor_hour(row.oldest_span)
        # Recent rollups were just written, so the oldest rollup is normally at or after
        # `window_start`; the gap ends where the rollups begin.
        gap_end = (
            window_start if row.oldest_rollup is None else min(row.oldest_rollup, window_start)
        )
        if gap_start < gap_end:
            gaps.append((gap_end - gap_start, row.project_id, gap_start, gap_end))
    # Smallest gap first; the project id makes the order deterministic.
    gaps.sort(key=lambda gap: (gap[0], gap[1]))

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
