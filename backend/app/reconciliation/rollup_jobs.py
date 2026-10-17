"""The daily cost rollup jobs (spec §10.3, §10.4).

`rollup_cost_daily` recomputes the day before yesterday, yesterday and today (UTC) every 15
minutes; `rollup_cost_daily_deep` recomputes the last eight days once a day, so a span that
arrives up to a week late is still counted. One query, with row-level security bypassed, lists
the projects that have an LLM span since the first day of the pass; each then refreshes in a
session of its own, bound to it alone, under the time bounds and stalest-first order of
`app.jobs.project_pass`. Both have a single attempt: the next pass is the retry.

`rollup_cost_daily_backfill` recomputes one project's window of at most 31 days, for a report
that asks for days no pass has built or a CSV upload over a long span. Its contract (kind,
payload, window) is `app.reconciliation.backfill`, which the request path imports instead of
this module; a malformed payload fails the job with `ValueError`.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project
from app.end_users.days import deep_days, midnight, recent_days
from app.jobs.context import TaskContext
from app.jobs.project_pass import (
    HARD_STOP_SECONDS,
    RUN_BUDGET_SECONDS,
    AttemptMemory,
    ProjectPass,
    run_project_pass,
)
from app.reconciliation.backfill import parse_backfill_payload
from app.reconciliation.job_queries import active_cost_project_ids
from app.reconciliation.rollups import refresh_cost_rollups

logger = structlog.get_logger(__name__)

# When this process last attempted each project, one memory per job (the two passes list
# different projects, and each forgets the projects its listing lacks); see
# `app.jobs.project_pass`.
_recent_memory = AttemptMemory()
_deep_memory = AttemptMemory()


async def run_rollup_cost_daily(context: TaskContext, _: dict[str, Any]) -> None:
    """The 15-minute pass: the last three days."""
    run = await rollup_all(
        context.session_factory, now=context.clock(), heartbeat=context.heartbeat
    )
    _log_pass("cost_rollups_refreshed", run)


async def run_rollup_cost_daily_deep(context: TaskContext, _: dict[str, Any]) -> None:
    """The nightly pass: the last eight days, under the same time bounds as the 15-minute one."""
    now = context.clock()
    run = await rollup_all(
        context.session_factory,
        now=now,
        days=deep_days(now),
        heartbeat=context.heartbeat,
        memory=_deep_memory,
    )
    _log_pass("cost_rollups_deep_refreshed", run)


async def run_rollup_cost_daily_backfill(context: TaskContext, payload: dict[str, Any]) -> None:
    """Recompute the payload's project and days, bound to that project."""
    project_id, days = parse_backfill_payload(payload)
    async with context.session_factory() as db:
        await bind_project(db, project_id)
        rows = await refresh_cost_rollups(db, project_id, days)
        await db.commit()
    logger.info(
        "cost_rollups_backfilled",
        project_id=str(project_id),
        day_from=days[0].isoformat(),
        day_to=days[-1].isoformat(),
        rows=rows,
    )


async def rollup_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    days: Sequence[date] | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
    memory: AttemptMemory | None = None,
) -> ProjectPass:
    """Recompute `days` (the last three, `recent_days(now)`, by default) for every project with
    an LLM span since the first of them. `heartbeat` runs between projects; `memory` is the
    job's stalest-first memory (the 15-minute pass's by default)."""
    window = list(days) if days is not None else recent_days(now)
    if not window:
        return ProjectPass()
    since = midnight(min(window))

    async def list_projects(db: AsyncSession) -> list[uuid.UUID]:
        return await active_cost_project_ids(db, since, now)

    async def refresh_one(db: AsyncSession, project_id: uuid.UUID) -> int:
        return await refresh_cost_rollups(db, project_id, window)

    return await run_project_pass(
        session_factory,
        list_projects=list_projects,
        run_project=refresh_one,
        memory=memory if memory is not None else _recent_memory,
        event="cost_rollups",
        heartbeat=heartbeat,
        budget_seconds=budget_seconds,
        hard_stop_seconds=hard_stop_seconds,
    )


def _log_pass(event: str, run: ProjectPass) -> None:
    logger.info(
        event,
        projects=run.projects,
        projects_failed=run.projects_failed,
        projects_left=run.projects_left,
        rows=run.rows,
    )
