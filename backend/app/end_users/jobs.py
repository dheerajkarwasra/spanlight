"""The `refresh_user_stats` job: recompute yesterday's and today's per-user stats, every 15 minutes.

One query, with row-level security bypassed, lists the projects that have a user-tagged trace since
the start of yesterday (UTC); each then refreshes in a session of its own, bound to it alone.
Yesterday is recomputed as well as today so a trace that arrives late for the day that just ended
is absorbed.

The job has a single attempt: the next pass is the retry. A pass stops starting projects after
`RUN_BUDGET_SECONDS` so it ends inside the worker's task timeout, and each project runs under its
own time bound so one slow project cannot hold the pass until the worker kills it. A project that
fails or runs out of time is logged and the pass goes on with the others.

Projects go stalest first: the worker process remembers when it last attempted each project, and
a project it has not attempted yet leads. A project a pass did not reach, or one that failed, is
therefore ahead of every project refreshed after it on the next pass. The memory is per process
and starts empty, so after a restart (or on another worker) the order is the query's, with every
project equal; no project waits behind the same tail pass after pass.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project
from app.end_users.days import midnight, recent_days
from app.end_users.job_queries import active_project_ids
from app.end_users.refresh import refresh_user_stats
from app.jobs.context import TaskContext

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s. No project starts after 40 s, and none runs past 45 s.
RUN_BUDGET_SECONDS = 40.0
HARD_STOP_SECONDS = 45.0
PROJECT_TIMEOUT_SECONDS = 30.0

# When this process last attempted each project (monotonic seconds); see the module docstring.
_last_attempted: dict[uuid.UUID, float] = {}


@dataclass(slots=True)
class RefreshPass:
    projects: int = 0
    projects_failed: int = 0
    projects_left: int = 0
    rows: int = 0


async def run_refresh_user_stats(context: TaskContext, _: dict[str, Any]) -> None:
    run = await refresh_all(
        context.session_factory, now=datetime.now(UTC), heartbeat=context.heartbeat
    )
    logger.info(
        "user_stats_refreshed",
        projects=run.projects,
        projects_failed=run.projects_failed,
        projects_left=run.projects_left,
        rows=run.rows,
    )


async def refresh_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
) -> RefreshPass:
    """Refresh yesterday and today for every active project. `heartbeat` runs between projects."""
    started = time.monotonic()
    deadline = started + budget_seconds
    hard_stop = started + hard_stop_seconds
    days = recent_days(now)
    async with session_factory() as db:
        # Since the start of the range recomputed, so a late trace of early yesterday counts.
        project_ids = stalest_first(await active_project_ids(db, midnight(days[0]), now))
    run = RefreshPass()
    for index, project_id in enumerate(project_ids):
        if heartbeat is not None:
            await heartbeat()
        if time.monotonic() >= deadline:
            run.projects_left = len(project_ids) - index
            logger.warning("user_stats_out_of_time", projects_left=run.projects_left)
            break
        _last_attempted[project_id] = time.monotonic()
        time_limit = min(PROJECT_TIMEOUT_SECONDS, hard_stop - time.monotonic())
        rows = await _refresh_one_project(session_factory, project_id, days, time_limit)
        if rows is None:
            run.projects_failed += 1
        else:
            run.projects += 1
            run.rows += rows
    return run


def stalest_first(project_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """`project_ids` with the never-attempted ones first, then the longest-ago attempted.

    Forgets projects that are no longer active, so the memory stays bounded. The sort is stable:
    projects this process has not attempted keep the query's order.
    """
    active = set(project_ids)
    for project_id in [known for known in _last_attempted if known not in active]:
        del _last_attempted[project_id]
    return sorted(project_ids, key=lambda project_id: _last_attempted.get(project_id, -1.0))


async def _refresh_one_project(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    days: list[date],
    time_limit: float,
) -> int | None:
    """Rows written for the project, or None when it could not be refreshed within `time_limit`
    seconds (logged; the transaction rolls back, so nothing half-written stays)."""
    try:
        async with asyncio.timeout(time_limit), session_factory() as db:
            await bind_project(db, project_id)
            rows = await refresh_user_stats(db, project_id, days)
            await db.commit()
            return rows
    except TimeoutError as exc:
        logger.error(
            "user_stats_project_timed_out",
            project_id=str(project_id),
            time_limit_s=round(time_limit),
            exc_info=exc,
        )
        return None
    except Exception as exc:  # isolate the project; the next pass tries it again
        logger.error(
            "user_stats_project_failed",
            project_id=str(project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return None
