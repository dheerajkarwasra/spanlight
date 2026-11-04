"""Refresh yesterday/today every 15 minutes, including promoted-key bootstrap.

Daily historical work has its own durable cursor in daily_jobs. This recent path
retains seven-day bootstrap and pruning for promoted metadata keys.
"""

import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.attribution.job_queries import active_project_ids, promoted_keys_of
from app.attribution.refresh import refresh_attribution
from app.core.observability import ATTRIBUTION_REFRESH_DURATION
from app.end_users.days import ONE_DAY, midnight
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.project_pass import (
    HARD_STOP_SECONDS,
    RUN_BUDGET_SECONDS,
    AttemptMemory,
    run_project_pass,
)
from app.jobs.scheduler import REFRESH_ATTRIBUTION, period_start

logger = structlog.get_logger(__name__)

# How many days, today included, a newly promoted key is backfilled over.
BACKFILL_DAYS = 7

# When this process last attempted each project; see `app.jobs.project_pass`.
_memory = AttemptMemory()


@dataclass(slots=True)
class RefreshPass:
    projects: int = 0
    projects_failed: int = 0
    projects_left: int = 0
    rows: int = 0
    folded: int = 0


def recent_days(now: datetime) -> list[date]:
    """Yesterday and today (UTC): the days every pass recomputes."""
    today = _today(now)
    return [today - ONE_DAY, today]


def backfill_days(now: datetime) -> list[date]:
    """`[today - 6, today]` (UTC): the days a newly promoted key is backfilled over."""
    today = _today(now)
    return [today - offset * ONE_DAY for offset in range(BACKFILL_DAYS - 1, -1, -1)]


def _today(now: datetime) -> date:
    return period_start(REFRESH_ATTRIBUTION, now).astimezone(UTC).date()


async def run_refresh_attribution(context: TaskContext, _: dict[str, Any]) -> JobOutcome | None:
    if not context.settings.attribution_enabled:
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    now = context.clock()
    run = await refresh_all(context.session_factory, now=now, heartbeat=context.heartbeat)
    logger.info(
        "attribution_refreshed",
        projects=run.projects,
        projects_failed=run.projects_failed,
        projects_left=run.projects_left,
        rows=run.rows,
        folded=run.folded,
    )

    return None


async def refresh_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    days: Sequence[date] | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
) -> RefreshPass:
    """Refresh `days` (`recent_days(now)` by default) for every project with a trace since the
    first of them, backfilling newly promoted keys over `backfill_days(now)`. `heartbeat` runs
    between projects."""
    window = list(days) if days is not None else recent_days(now)
    if not window:
        return RefreshPass()
    since = midnight(min(window))
    backfill = backfill_days(now)
    folded = 0

    async def list_projects(db: AsyncSession) -> list[uuid.UUID]:
        return await active_project_ids(db, since, now)

    async def refresh_one(db: AsyncSession, project_id: uuid.UUID) -> int:
        nonlocal folded
        promoted = await promoted_keys_of(db, project_id) or []
        started = time.monotonic()
        result = await refresh_attribution(db, project_id, promoted, window, backfill_days=backfill)
        ATTRIBUTION_REFRESH_DURATION.observe(time.monotonic() - started)
        folded += result.folded
        return result.written

    run = await run_project_pass(
        session_factory,
        list_projects=list_projects,
        run_project=refresh_one,
        memory=_memory,
        event="attribution",
        heartbeat=heartbeat,
        budget_seconds=budget_seconds,
        hard_stop_seconds=hard_stop_seconds,
    )
    return RefreshPass(run.projects, run.projects_failed, run.projects_left, run.rows, folded)
