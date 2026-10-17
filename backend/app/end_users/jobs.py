"""The user stats refresh jobs: the last three days every 15 minutes, the last eight nightly.

`refresh_user_stats` recomputes the day before yesterday, yesterday and today (UTC) every 15
minutes; `refresh_user_stats_deep` recomputes the last eight days once a day. One query, with
row-level security bypassed, lists the projects that have a user-tagged trace since the first day
of the pass; each then refreshes in a session of its own, bound to it alone. A trace counts for
the day it started, so a span that arrives late for an older trace is absorbed only while that
day is inside a pass: within three days by the next 15-minute pass, within eight by the next
nightly one, and never after that.

The job has a single attempt: the next pass is the retry. The per-project loop, its time bounds
and the stalest-first order are `app.jobs.project_pass`'s, shared with the daily cost rollup.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.end_users.days import deep_days, midnight, recent_days
from app.end_users.job_queries import active_project_ids
from app.end_users.refresh import refresh_user_stats
from app.jobs.context import TaskContext
from app.jobs.project_pass import (
    HARD_STOP_SECONDS,
    RUN_BUDGET_SECONDS,
    AttemptMemory,
    ProjectPass,
    run_project_pass,
)

logger = structlog.get_logger(__name__)

# When this process last attempted each project; see `app.jobs.project_pass`.
_memory = AttemptMemory()


async def run_refresh_user_stats(context: TaskContext, _: dict[str, Any]) -> None:
    """The 15-minute pass: the last three days."""
    run = await refresh_all(
        context.session_factory, now=datetime.now(UTC), heartbeat=context.heartbeat
    )
    _log_pass("user_stats_refreshed", run)


async def run_refresh_user_stats_deep(context: TaskContext, _: dict[str, Any]) -> None:
    """The nightly pass: the last eight days, under the same time bounds as the 15-minute one."""
    now = datetime.now(UTC)
    run = await refresh_all(
        context.session_factory, now=now, days=deep_days(now), heartbeat=context.heartbeat
    )
    _log_pass("user_stats_deep_refreshed", run)


def _log_pass(event: str, run: ProjectPass) -> None:
    logger.info(
        event,
        projects=run.projects,
        projects_failed=run.projects_failed,
        projects_left=run.projects_left,
        rows=run.rows,
    )


async def refresh_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    days: Sequence[date] | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
) -> ProjectPass:
    """Refresh `days` (the last three, `recent_days(now)`, by default) for every active project.

    `heartbeat` runs between projects.
    """
    window = list(days) if days is not None else recent_days(now)
    if not window:
        return ProjectPass()
    # Since the start of the range recomputed, so a late trace of its first day counts.
    since = midnight(min(window))

    async def list_projects(db: AsyncSession) -> list[uuid.UUID]:
        return await active_project_ids(db, since, now)

    async def refresh_one(db: AsyncSession, project_id: uuid.UUID) -> int:
        return await refresh_user_stats(db, project_id, window)

    return await run_project_pass(
        session_factory,
        list_projects=list_projects,
        run_project=refresh_one,
        memory=_memory,
        event="user_stats",
        heartbeat=heartbeat,
        budget_seconds=budget_seconds,
        hard_stop_seconds=hard_stop_seconds,
    )
