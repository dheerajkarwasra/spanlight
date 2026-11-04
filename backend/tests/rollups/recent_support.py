"""Real registered jobs and legal historical admission for recent closure tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import Settings
from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.jobs.queue import complete
from app.jobs.tasks import TASKS
from tests.attribution.daily_support import queued
from tests.jobs.bypass_support import SessionFactory, fetch


async def run_recent(
    factory: SessionFactory, settings: Settings, now: datetime | None = None
) -> None:
    job = await queued(factory, "rollup_hourly", {})
    moment = now or datetime.now(UTC)
    await TASKS[job.kind](TaskContext(job, settings, factory, clock=lambda: moment), {})
    async with factory() as db:
        await complete(db, job)


async def state(factory: SessionFactory, project: UUID) -> dict[str, Any]:
    [row] = await fetch(
        factory,
        "SELECT * FROM aggregate_job_progress WHERE project_id=:p AND kind='rollup_recent'",
        {"p": project},
    )
    return row


async def admit_at(factory: SessionFactory, project: UUID, hour: datetime) -> None:
    """Fixture-only schema-owner setup, with no concurrent writer or evidence changes.

    Both immutable epochs describe the same old admission instant; restore the trigger
    before commit. Execution/recovery always use the genuine guard and public handlers.
    """
    admission = hour + timedelta(hours=2)
    async with factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "ALTER TABLE aggregate_job_progress DISABLE TRIGGER "
                "aggregate_job_progress_immutable"
            )
        )
        await db.execute(
            text("""UPDATE aggregate_job_progress SET
          first_period=CASE WHEN kind='attribution_daily' THEN CAST(:day AS date) END,
          settled_period=CASE WHEN kind='attribution_daily' THEN CAST(:day AS date)-1 END,
          admitted_hour=CASE WHEN kind='rollup_recent' THEN CAST(:hour AS timestamptz) END,
          next_closed_hour=CASE WHEN kind='rollup_recent' THEN CAST(:hour AS timestamptz) END
          WHERE project_id=:p"""),
            {"p": project, "day": admission.date(), "hour": hour},
        )
        await db.execute(
            text(
                "ALTER TABLE aggregate_job_progress ENABLE TRIGGER aggregate_job_progress_immutable"
            )
        )
        await db.commit()
