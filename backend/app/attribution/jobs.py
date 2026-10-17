"""The attribution refresh job: yesterday and today every 15 minutes, a week more once a day.

`refresh_attribution` recomputes yesterday and today (UTC) on every pass. The first pass of each
UTC day (the one whose 15-minute period starts at 00:00, detected from the clock alone, so no
state is kept anywhere) also recomputes `[today - 7, today - 2]`, to absorb spans that arrived
late for those days and price-change replays. One query, with row-level security bypassed, lists
the projects that have a trace since the first day of the pass; each then refreshes in a session
of its own, bound to it alone, under the time bounds and stalest-first order of
`app.jobs.project_pass`. A promoted key with no rows in the last seven days (just promoted) is
backfilled over `[today - 6, today]`, and the rows of keys no longer promoted are deleted
(spec §5.1). The job has a single attempt: the next pass is the retry.
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


def deep_days(now: datetime) -> list[date]:
    """`[today - 7, today - 2]` (UTC): the days the first pass of each day recomputes as well."""
    today = _today(now)
    return [today - offset * ONE_DAY for offset in range(7, 1, -1)]


def backfill_days(now: datetime) -> list[date]:
    """`[today - 6, today]` (UTC): the days a newly promoted key is backfilled over."""
    today = _today(now)
    return [today - offset * ONE_DAY for offset in range(BACKFILL_DAYS - 1, -1, -1)]


def is_first_pass_of_day(now: datetime) -> bool:
    """Whether the pass at `now` is the first of its UTC day: its period starts at 00:00."""
    this_period = period_start(REFRESH_ATTRIBUTION, now)
    previous_period = period_start(REFRESH_ATTRIBUTION, now - REFRESH_ATTRIBUTION.every)
    return this_period.astimezone(UTC).date() != previous_period.astimezone(UTC).date()


def pass_days(now: datetime) -> list[date]:
    """The days the pass at `now` recomputes: the recent ones, and the deep ones once a day."""
    recent = recent_days(now)
    return deep_days(now) + recent if is_first_pass_of_day(now) else recent


def _today(now: datetime) -> date:
    return period_start(REFRESH_ATTRIBUTION, now).astimezone(UTC).date()


async def run_refresh_attribution(context: TaskContext, _: dict[str, Any]) -> None:
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


async def refresh_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    days: Sequence[date] | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
) -> RefreshPass:
    """Refresh `days` (`pass_days(now)` by default) for every project with a trace since the
    first of them, backfilling newly promoted keys over `backfill_days(now)`. `heartbeat` runs
    between projects."""
    window = list(days) if days is not None else pass_days(now)
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
