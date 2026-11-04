"""Which cleanup class and scope the retention job serves next (`retention_progress`).

One row per class holds the ordering token of its last opportunity and the project or billing
source it visited last. Discovery is one statement in a short transaction: lock the class with
the lowest token (`SKIP LOCKED`, so another worker holding it is skipped, not waited for), pick
the next scope of its roster by UUID keyset (the first one again when the cursor is past the
last, or when the roster is empty: an empty roster still rotates), and move the class to the end
of the order. The job then checks its lease is still live and commits, before it opens any
source lock or deletes anything. Deleting the cursor's project does not break `id > cursor`; a
project created behind the cursor waits for the wrap.

Serialized, this gives every class an opportunity within 14 selections and every (class, scope)
pair one within 14 x roster size, across restarts. Concurrent workers keep rotation durable but
not that count. Opportunities, never deletion success: a batch that times out keeps its turn
spent, and the scope waits for the next wrap.
"""

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.aggregate_progress import DISCOVERY_SECONDS, remaining, set_limits
from app.jobs.context import TaskContext
from app.jobs.fencing import lock_live_job

# Every class, in the order migration 1035 seeded them; the job refuses to run with any missing.
CLASSES: tuple[str, ...] = (
    "trace_cascades",
    "hourly_aggregates",
    "resolved_insights",
    "detector_runs",
    "user_stats",
    "daily_cost",
    "daily_attribution",
    "scores",
    "online_evaluator_runs",
    "annotation_queue_items",
    "provider_cost_lines",
    "fetch_runs",
    "provider_incidents",
    "audit_events",
)
SOURCE_CLASSES = frozenset({"provider_cost_lines", "fetch_runs"})
GLOBAL_CLASSES = frozenset({"provider_incidents", "audit_events"})
PROJECT_CLASSES = frozenset(CLASSES) - SOURCE_CLASSES - GLOBAL_CLASSES


@dataclass(frozen=True)
class Opportunity:
    cleanup_class: str
    # The project or billing source to serve; None for a global class or an empty roster.
    scope_id: uuid.UUID | None


# `id > NULL` matches nothing, so a NULL cursor and a cursor past the last id both fall through
# `COALESCE` to the roster's first id. The class lists are literal: no table name is interpolated.
_DISCOVER = text("""
    WITH candidate AS MATERIALIZED (
        SELECT class, after_scope_id FROM retention_progress
        ORDER BY attempt_order, class LIMIT 1 FOR UPDATE SKIP LOCKED
    ), chosen AS (
        SELECT c.class, CASE
            WHEN c.class IN ('provider_cost_lines', 'fetch_runs') THEN COALESCE(
                (SELECT id FROM billing_sources WHERE id > c.after_scope_id ORDER BY id LIMIT 1),
                (SELECT id FROM billing_sources ORDER BY id LIMIT 1))
            WHEN c.class IN ('provider_incidents', 'audit_events') THEN NULL
            ELSE COALESCE(
                (SELECT id FROM projects WHERE id > c.after_scope_id ORDER BY id LIMIT 1),
                (SELECT id FROM projects ORDER BY id LIMIT 1))
        END AS scope_id
        FROM candidate c
    )
    UPDATE retention_progress p
    SET after_scope_id = chosen.scope_id,
        attempt_order = nextval('public.retention_attempt_order_seq'),
        last_attempted_at = clock_timestamp()
    FROM chosen WHERE p.class = chosen.class
    RETURNING p.class, p.after_scope_id
""")


async def require_classes(db: AsyncSession) -> None:
    """Fail the run visibly when a cursor row is missing, rather than skipping its class."""
    present = set(await db.scalars(text("SELECT class FROM retention_progress")))
    missing = sorted(set(CLASSES) - present)
    if missing:
        raise RuntimeError(f"retention_progress is missing cleanup classes: {missing}")


async def next_opportunity(context: TaskContext, deadline: float) -> Opportunity | None:
    """Rotate one class and commit the opportunity under a live lease, before any source work.

    None when every class row is held by another worker right now.
    """
    seconds = min(DISCOVERY_SECONDS, remaining(deadline))
    async with asyncio.timeout(seconds), context.session_factory() as db:
        try:
            await set_limits(db, seconds)
            row = (await db.execute(_DISCOVER)).first()
            if row is None:
                return None
            await lock_live_job(db, context.job)
            await db.commit()
            return Opportunity(cleanup_class=row[0], scope_id=row[1])
        except BaseException:
            await db.invalidate()
            raise
