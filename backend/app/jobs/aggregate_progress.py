"""Bounded worker-only discovery, durable attempt ordering and transaction limits."""

import asyncio
import uuid
from typing import Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.jobs.fencing import lock_live_job
from app.jobs.queue import extend_lease

# Local PG17 calibration: 100 dispatches < 0.5s; 50k-source day < 0.8s.
# Cooperative bounds, not a per-tenant completion SLA. See aggregate-repair runbook.
START_SECONDS = 30.0
OUTER_SECONDS = 40.0
TRANSACTION_SECONDS = 5.0
DISCOVERY_SECONDS = 1.0
HEARTBEAT_SECONDS = 1.0
LOCK_MILLISECONDS = 250
MAX_CANDIDATES = 100

_DISCOVER = text("""
    WITH candidate AS MATERIALIZED (
        SELECT project_id, kind FROM aggregate_job_progress
        WHERE kind = :kind AND project_id <> ALL(CAST(:visited AS uuid[]))
        ORDER BY attempt_order, project_id LIMIT 1 FOR UPDATE SKIP LOCKED
    )
    UPDATE aggregate_job_progress g
    SET attempt_order = nextval('public.aggregate_job_attempt_seq'::regclass),
        last_attempted_at = clock_timestamp()
    FROM candidate c WHERE g.project_id = c.project_id AND g.kind = c.kind
    RETURNING g.project_id
""")


def remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


async def set_limits(db: AsyncSession, seconds: float) -> None:
    """Positive local limits; the surrounding deadline also bounds all statements together."""
    milliseconds = max(1, int(seconds * 1000))
    await db.execute(
        text(
            "SELECT set_config('statement_timeout', :statement, true), "
            "set_config('lock_timeout', :lock, true)"
        ),
        {"statement": str(milliseconds), "lock": str(min(LOCK_MILLISECONDS, milliseconds))},
    )


async def heartbeat(context: TaskContext, deadline: float) -> None:
    seconds = min(HEARTBEAT_SECONDS, remaining(deadline))
    async with asyncio.timeout(seconds), context.session_factory() as db:
        try:
            await set_limits(db, seconds)
            await extend_lease(db, context.job)
        except BaseException:
            await db.invalidate()
            raise


async def next_candidate(
    context: TaskContext,
    kind: Literal["attribution_daily", "rollup_recent"],
    visited: list[uuid.UUID],
    deadline: float,
) -> uuid.UUID | None:
    """Commit one fenced attempt before opening any source/domain transaction."""
    seconds = min(DISCOVERY_SECONDS, remaining(deadline))
    async with asyncio.timeout(seconds), context.session_factory() as db:
        try:
            await set_limits(db, seconds)
            await bypass_rls(db)
            project = await db.scalar(_DISCOVER, {"kind": kind, "visited": visited})
            await lock_live_job(db, context.job)
            await db.commit()
            return cast(uuid.UUID | None, project)
        except BaseException:
            await db.invalidate()
            raise
