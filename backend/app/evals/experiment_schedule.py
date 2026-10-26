"""The weekly experiment reruns: the daily `schedule_experiments` job and one experiment's
`run_scheduled_experiment`.

`schedule_experiments` runs once a UTC day from 04:00 (`SCHEDULE_EXPERIMENTS`, scheduled only
with `EXPERIMENT_SCHEDULES_ENABLED`). It lists every experiment with a schedule, the one read in
this feature that needs the row-level security bypass, and enqueues one `run_scheduled_experiment`
per experiment with a dedupe key per ISO week, so however many workers and daily passes there are,
an experiment gets one run a week. Three attempts: the listing is cheap and the children dedupe.

`run_scheduled_experiment` binds its project and calls the same `start_run` a person does, with
`trigger = "schedule"`; the run's `run_experiment` job then pays the providers under the
experiment's cost cap. One attempt: a second would queue a second paid run. A manual run already
active ends the job `done` / `skipped_run_in_progress`, and next week tries again; an experiment
deleted since the listing fails the job, visible in the job table. When such a run ends `done`,
`app.evals.experiment_regression` compares it with its baseline.
"""

import uuid
from datetime import datetime
from typing import Any

import structlog

from app.db.rls import bind_project, bypass_rls
from app.evals import experiment_queries as queries
from app.evals.experiment_errors import RunInProgressError
from app.evals.experiments import ScheduleActor, start_run
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import enqueue

logger = structlog.get_logger(__name__)

SCHEDULE_JOB = "schedule_experiments"
RUN_SCHEDULED_JOB = "run_scheduled_experiment"
# One attempt: the child queues a paid run.
RUN_SCHEDULED_ATTEMPTS = 1


def iso_week(now: datetime) -> str:
    """`2026-W44`: the ISO week `now` falls in."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02}"


def scheduled_run_key(experiment_id: uuid.UUID, now: datetime) -> str:
    """One key per experiment per ISO week, whatever day of the week the parent runs."""
    return f"scheduled_experiment:{experiment_id}:{iso_week(now)}"


async def run_schedule_experiments(context: TaskContext, _: dict[str, Any]) -> None:
    """Enqueue this ISO week's run for every scheduled experiment that has none yet."""
    now = context.clock()
    created = 0
    async with context.session_factory() as db, db.begin():
        await bypass_rls(db)
        scheduled = await queries.scheduled_experiments(db)
        for experiment_id, project_id, org_id in scheduled:
            created += await enqueue(
                db,
                RUN_SCHEDULED_JOB,
                {
                    "experiment_id": str(experiment_id),
                    "project_id": str(project_id),
                    "org_id": str(org_id),
                },
                dedupe_key=scheduled_run_key(experiment_id, now),
                max_attempts=RUN_SCHEDULED_ATTEMPTS,
            )
    logger.info(
        "scheduled_experiments_enqueued",
        week=iso_week(now),
        experiments=len(scheduled),
        created=created,
    )


async def run_run_scheduled_experiment(
    context: TaskContext, payload: dict[str, Any]
) -> JobOutcome | None:
    """Queue the payload's experiment run as the app, bound to its project."""
    experiment_id = uuid.UUID(str(payload["experiment_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    actor = ScheduleActor(org_id=uuid.UUID(str(payload["org_id"])))
    async with context.session_factory() as db, db.begin():
        await bind_project(db, project_id)
        try:
            run = await start_run(
                db, project_id, actor, experiment_id, now=context.clock(), trigger="schedule"
            )
        except RunInProgressError:
            logger.info(
                "scheduled_experiment_skipped_run_in_progress",
                project_id=str(project_id),
                experiment_id=str(experiment_id),
            )
            return JobOutcome.SKIPPED_RUN_IN_PROGRESS
    logger.info(
        "scheduled_experiment_run_queued",
        project_id=str(project_id),
        experiment_id=str(experiment_id),
        run_id=str(run.id),
    )
    return None
