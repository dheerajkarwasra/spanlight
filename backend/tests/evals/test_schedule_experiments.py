"""The daily `schedule_experiments` job queues one paid run per scheduled experiment per ISO week,
however often it runs, and none for an experiment without a schedule (G-012)."""

from datetime import UTC, datetime

from app.config import Settings
from app.evals.experiment_schedule import (
    RUN_SCHEDULED_JOB,
    SCHEDULE_JOB,
    run_schedule_experiments,
    scheduled_run_key,
)
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob
from app.jobs.scheduler import SCHEDULE_EXPERIMENTS, periodic_jobs
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row

# A Tuesday and the Friday of the same ISO week (2026-W44).
TUESDAY = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 10, 30, 4, 0, tzinfo=UTC)


async def test_two_passes_in_one_week_queue_one_run_for_the_scheduled_experiment_only(
    session_factory: SessionFactory, settings: Settings
) -> None:
    project_a, project_b = await new_projects(session_factory, 2)
    scheduled = await seed_row(session_factory, "experiments", project_a)
    unscheduled = await seed_row(session_factory, "experiments", project_b)
    await execute(
        session_factory,
        "UPDATE experiments SET schedule = 'weekly' WHERE id = :id",
        {"id": scheduled["id"]},
    )

    for moment in (TUESDAY, FRIDAY):
        await run_schedule_experiments(_context(session_factory, settings, moment), {})

    children = await fetch(
        session_factory,
        "SELECT payload, dedupe_key, max_attempts FROM jobs WHERE kind = :kind",
        {"kind": RUN_SCHEDULED_JOB},
    )
    assert len(children) == 1, children
    (child,) = children
    assert child["payload"]["experiment_id"] == str(scheduled["id"])
    assert child["payload"]["project_id"] == str(scheduled["project_id"])
    assert child["dedupe_key"] == scheduled_run_key(scheduled["id"], TUESDAY)
    assert child["dedupe_key"].endswith(":2026-W44")
    assert child["max_attempts"] == 1  # a paid run is never retried
    assert str(unscheduled["id"]) not in {c["payload"]["experiment_id"] for c in children}
    # The parent itself runs only on a server that turned schedules on.
    assert SCHEDULE_EXPERIMENTS not in periodic_jobs(settings)
    on = settings.model_copy(update={"experiment_schedules_enabled": True})
    assert SCHEDULE_EXPERIMENTS in periodic_jobs(on)


def _context(session_factory: SessionFactory, settings: Settings, now: datetime) -> TaskContext:
    job = ClaimedJob(id=0, kind=SCHEDULE_JOB, payload={}, attempts=1, max_attempts=3, fence=0)
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=lambda: now
    )
