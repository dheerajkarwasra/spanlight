"""Queue age and depth gauges, sampled by the worker on its heartbeat tick.

One statement over the two partial job indexes (`jobs_queued_run_after_idx`,
`jobs_running_lease_idx`) groups due queued rows and expired reclaimable leases by kind, in
database time. Kinds are the worker's registry frozen at start-up; any other kind lands in
`other`, so a queued kind no worker serves stays visible without new series. A complete sample
sets every kind, so a drained kind reads zero. A failed or timed-out sample removes the series
instead of leaving a stale or zero value, and counts in `spanlight_jobs_queue_samples_total`.
Each worker samples the whole queue, so dashboards with several workers take `max`, not `sum`.
"""

import asyncio
from collections.abc import Collection

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import (
    JOBS_QUEUE_SAMPLES,
    JOBS_QUEUED_DUE,
    JOBS_QUEUED_OLDEST_AGE,
    JOBS_RECLAIMABLE,
    JOBS_RECLAIMABLE_OLDEST_AGE,
)
from app.db.timeouts import set_statement_limit

logger = structlog.get_logger(__name__)

# Separate from the claim budgets (7C2): a slow sample is dropped, never a reason to wait.
SAMPLE_STATEMENT_MS = 1000
SAMPLE_SECONDS = 2.0
_GAUGES = (JOBS_QUEUED_DUE, JOBS_QUEUED_OLDEST_AGE, JOBS_RECLAIMABLE, JOBS_RECLAIMABLE_OLDEST_AGE)
# Same eligibility as the claim: due queued rows, and expired leases with attempts left.
# Expired leases of exhausted jobs are failures-in-waiting, not reclaimable work.
_SAMPLE = text(
    """
    SELECT CASE WHEN kind = ANY (CAST(:kinds AS text[])) THEN kind ELSE 'other' END AS kind,
           count(*) FILTER (WHERE status = 'queued') AS queued,
           coalesce(max(extract(epoch FROM now() - run_after))
                    FILTER (WHERE status = 'queued'), 0) AS queued_age,
           count(*) FILTER (WHERE status = 'running') AS reclaimable,
           coalesce(max(extract(epoch FROM now() - lease_until))
                    FILTER (WHERE status = 'running'), 0) AS reclaimable_age
    FROM jobs
    WHERE (status = 'queued' AND run_after <= now())
       OR (status = 'running' AND lease_until < now() AND attempts < max_attempts)
    GROUP BY 1
    """
)


async def sample_queue_gauges(
    session_factory: async_sessionmaker[AsyncSession], kinds: Collection[str]
) -> None:
    """Set the queue gauges for `kinds` plus `other`, or clear them when the sample fails."""
    try:
        async with asyncio.timeout(SAMPLE_SECONDS), session_factory() as db:
            try:
                await set_statement_limit(db, SAMPLE_STATEMENT_MS)
                rows = (await db.execute(_SAMPLE, {"kinds": sorted(kinds)})).all()
                await db.rollback()
            except BaseException:
                # A cancelled sample discards its connection rather than awaiting a rollback.
                await db.invalidate()
                raise
    except Exception as exc:  # noqa: BLE001 - a failed sample must never take the worker down
        for gauge in _GAUGES:
            gauge.clear()
        JOBS_QUEUE_SAMPLES.labels("failed").inc()
        logger.warning("queue_gauges_sample_failed", error_type=type(exc).__name__)
        return
    seen = {row.kind: row for row in rows}
    for kind in (*kinds, "other"):
        row = seen.get(kind)
        JOBS_QUEUED_DUE.labels(kind).set(row.queued if row else 0)
        JOBS_QUEUED_OLDEST_AGE.labels(kind).set(row.queued_age if row else 0)
        JOBS_RECLAIMABLE.labels(kind).set(row.reclaimable if row else 0)
        JOBS_RECLAIMABLE_OLDEST_AGE.labels(kind).set(row.reclaimable_age if row else 0)
    JOBS_QUEUE_SAMPLES.labels("ok").inc()
