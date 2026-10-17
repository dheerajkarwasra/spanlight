"""One pass of a per-project worker job: list the projects, then run each bound to itself.

The user stats refresh and the daily cost rollup share this loop. The listing runs in a session of
its own (the caller's query decides whether it needs the row-level security bypass); each project
then runs in a fresh session bound to it alone and commits on its own, so one project's failure
rolls back nothing but its own work.

A pass stops starting projects after `budget_seconds` so it ends inside the worker's task
timeout, and each project runs under its own time bound so one slow project cannot hold the pass
until the worker kills it. A project that fails or runs out of time is logged and the pass goes
on with the others; these jobs have a single attempt, and the next pass is the retry.

Projects go stalest first: each job keeps an `AttemptMemory` of when this process last attempted
each project, and a project it has not attempted yet leads. A project a pass did not reach, or
one that failed, is therefore ahead of every project run after it on the next pass. The memory is
per process and starts empty, so after a restart (or on another worker) the order is the
listing's, with every project equal; no project waits behind the same tail pass after pass.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s. No project starts after 40 s, and none runs past 45 s.
RUN_BUDGET_SECONDS = 40.0
HARD_STOP_SECONDS = 45.0
PROJECT_TIMEOUT_SECONDS = 30.0

ListProjects = Callable[[AsyncSession], Awaitable[list[uuid.UUID]]]
# Runs in a session bound to the project and returns the rows it wrote; the pass commits.
RunProject = Callable[[AsyncSession, uuid.UUID], Awaitable[int]]


@dataclass(slots=True)
class ProjectPass:
    projects: int = 0
    projects_failed: int = 0
    projects_left: int = 0
    rows: int = 0


class AttemptMemory:
    """When this process last attempted each project (monotonic seconds), for one job."""

    def __init__(self) -> None:
        self._last_attempted: dict[uuid.UUID, float] = {}

    def stalest_first(self, project_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """`project_ids` with the never-attempted ones first, then the longest-ago attempted.

        Forgets projects that are no longer listed, so the memory stays bounded. The sort is
        stable: projects this process has not attempted keep the listing's order.
        """
        listed = set(project_ids)
        for project_id in [known for known in self._last_attempted if known not in listed]:
            del self._last_attempted[project_id]
        return sorted(
            project_ids, key=lambda project_id: self._last_attempted.get(project_id, -1.0)
        )

    def attempted(self, project_id: uuid.UUID) -> None:
        self._last_attempted[project_id] = time.monotonic()


async def run_project_pass(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    list_projects: ListProjects,
    run_project: RunProject,
    memory: AttemptMemory,
    event: str,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
    hard_stop_seconds: float = HARD_STOP_SECONDS,
) -> ProjectPass:
    """Run `run_project` for every listed project, stalest first, within the time bounds.

    `event` prefixes the log events (`<event>_out_of_time`, `<event>_project_timed_out`,
    `<event>_project_failed`). `heartbeat` runs between projects.
    """
    started = time.monotonic()
    deadline = started + budget_seconds
    hard_stop = started + hard_stop_seconds
    async with session_factory() as db:
        project_ids = memory.stalest_first(await list_projects(db))
    run = ProjectPass()
    for index, project_id in enumerate(project_ids):
        if heartbeat is not None:
            await heartbeat()
        if time.monotonic() >= deadline:
            run.projects_left = len(project_ids) - index
            logger.warning(f"{event}_out_of_time", projects_left=run.projects_left)
            break
        memory.attempted(project_id)
        time_limit = min(PROJECT_TIMEOUT_SECONDS, hard_stop - time.monotonic())
        rows = await _run_one_project(session_factory, project_id, run_project, event, time_limit)
        if rows is None:
            run.projects_failed += 1
        else:
            run.projects += 1
            run.rows += rows
    return run


async def _run_one_project(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    run_project: RunProject,
    event: str,
    time_limit: float,
) -> int | None:
    """Rows written for the project, or None when it could not run within `time_limit` seconds
    (logged; the transaction rolls back, so nothing half-written stays)."""
    try:
        async with asyncio.timeout(time_limit), session_factory() as db:
            await bind_project(db, project_id)
            rows = await run_project(db, project_id)
            await db.commit()
            return rows
    except TimeoutError as exc:
        logger.error(
            f"{event}_project_timed_out",
            project_id=str(project_id),
            time_limit_s=round(time_limit),
            exc_info=exc,
        )
        return None
    except Exception as exc:  # isolate the project; the next pass tries it again
        logger.error(
            f"{event}_project_failed",
            project_id=str(project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return None
