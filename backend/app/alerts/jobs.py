"""The `evaluate_alerts` job: evaluate every enabled alert rule, once a minute.

One query, with row-level security bypassed, lists the projects that have enabled rules; each
project is then evaluated in sessions bound to it alone (see `evaluation.py`), one transaction
per rule. All reads of a pass share one `now` and one metric cache, so rules over the same
metric, window and filters read the database once.

The job has a single attempt: the next pass a minute later is the retry. A pass stops starting
projects, and rules within a project, after `RUN_BUDGET_SECONDS` so it ends inside the worker's
task timeout; projects and rules that waited longest go first, so the ones a slow pass did not
reach lead the next one.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.evaluation import ProjectOutcome, evaluate_project
from app.alerts.job_queries import projects_with_enabled_rules
from app.alerts.metrics import MetricCache
from app.core.observability import ALERT_EVALUATION_DURATION
from app.jobs.context import TaskContext

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s; the rule in flight when the budget runs out has the
# remaining 10 s to finish.
RUN_BUDGET_SECONDS = 40.0


@dataclass(slots=True)
class EvaluationRun:
    projects: int = 0
    projects_failed: int = 0
    projects_left: int = 0
    rules: int = 0
    rules_left: int = 0
    transitions: int = 0
    errors: int = 0

    def add(self, outcome: ProjectOutcome) -> None:
        self.projects += 1
        self.rules += outcome.evaluated
        self.rules_left += outcome.left
        self.transitions += outcome.transitions
        self.errors += outcome.errors


async def run_evaluate_alerts(context: TaskContext, _: dict[str, Any]) -> None:
    started = time.perf_counter()
    try:
        run = await evaluate_all(
            context.session_factory,
            context.settings,
            now=datetime.now(UTC),
            heartbeat=context.heartbeat,
        )
    finally:
        ALERT_EVALUATION_DURATION.observe(time.perf_counter() - started)
    logger.info(
        "alerts_evaluated",
        projects=run.projects,
        projects_failed=run.projects_failed,
        projects_left=run.projects_left,
        rules=run.rules,
        rules_left=run.rules_left,
        transitions=run.transitions,
        errors=run.errors,
    )


async def evaluate_all(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
    *,
    now: datetime,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
) -> EvaluationRun:
    """Evaluate every project with enabled rules at `now`. `heartbeat` runs between projects."""
    deadline = time.monotonic() + budget_seconds
    async with session_factory() as db:
        project_ids = await projects_with_enabled_rules(db)
    cache = MetricCache(now)
    run = EvaluationRun()
    for index, project_id in enumerate(project_ids):
        if time.monotonic() >= deadline:
            run.projects_left = len(project_ids) - index
            logger.warning(
                "alert_evaluation_out_of_time",
                projects_left=run.projects_left,
                rules_left=run.rules_left,
            )
            break
        if heartbeat is not None:
            await heartbeat()
        outcome = await _evaluate_one_project(
            session_factory, settings, project_id, now, cache, deadline
        )
        if outcome is None:
            run.projects_failed += 1
        else:
            run.add(outcome)
    return run


async def _evaluate_one_project(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
    project_id: uuid.UUID,
    now: datetime,
    cache: MetricCache,
    deadline: float,
) -> ProjectOutcome | None:
    """The project's outcome, or None when the project itself could not be read (logged).

    Rule failures are handled inside; this only catches a failure around them, such as the
    connection dropping, so one project cannot end the pass for the others.
    """
    try:
        async with session_factory() as db:
            return await evaluate_project(
                db, project_id, now, cache, settings=settings, deadline=deadline
            )
    except Exception as exc:  # isolate the project; the next pass tries it again
        logger.error(
            "alert_project_evaluation_failed",
            project_id=str(project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return None
