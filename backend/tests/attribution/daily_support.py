"""Real queue/registered-handler helpers for daily continuation tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import Settings
from app.db.rls import bypass_rls
from app.end_users.days import midnight
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, complete, enqueue
from app.jobs.tasks import TASKS
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.jobs.bypass_support import SessionFactory, fetch


def today() -> date:
    return datetime.now(UTC).date()


async def queued(factory: SessionFactory, kind: str, payload: dict[str, Any]) -> ClaimedJob:
    async with factory() as db:
        await enqueue(db, kind, payload, max_attempts=1)
        await db.commit()
        job = await claim_next(db, supported_kinds={kind})
    assert job is not None
    return job


async def run(
    factory: SessionFactory, settings: Settings, job: ClaimedJob, *, finish: bool = True
) -> None:
    outcome = await TASKS[job.kind](TaskContext(job, settings, factory), job.payload)
    if finish:
        async with factory() as db:
            if outcome is None:
                await complete(db, job)
            else:
                await complete(db, job, outcome)


async def dispatch(factory: SessionFactory, settings: Settings, period: date | None = None) -> None:
    job = await queued(
        factory, "schedule_attribution_daily", {"scheduled_period": (period or today()).isoformat()}
    )
    await run(factory, settings, job)


async def child(factory: SessionFactory) -> ClaimedJob:
    async with factory() as db:
        job = await claim_next(db, supported_kinds={"refresh_attribution_daily"})
    assert job is not None
    return job


async def state(factory: SessionFactory, project: UUID) -> dict[str, Any]:
    [row] = await fetch(
        factory,
        "SELECT * FROM aggregate_job_progress WHERE project_id=:p AND kind='attribution_daily'",
        {"p": project},
    )
    return row


async def scheduled_rows(factory: SessionFactory) -> list[dict[str, Any]]:
    return await fetch(
        factory, "SELECT * FROM jobs WHERE kind='refresh_attribution_daily' ORDER BY id"
    )


async def fail_before_cursor(factory: SessionFactory) -> None:
    async with factory() as db:
        await db.execute(
            text("""
          CREATE FUNCTION fail_daily_progress() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.next_day IS DISTINCT FROM OLD.next_day THEN
                RAISE EXCEPTION 'injected before cursor commit';
            END IF;
            RETURN NEW;
          END $$;
          CREATE TRIGGER fail_daily_progress BEFORE UPDATE ON aggregate_job_progress
          FOR EACH ROW EXECUTE FUNCTION fail_daily_progress();
        """)
        )
        await db.commit()


async def remove_cursor_failure(factory: SessionFactory) -> None:
    async with factory() as db:
        await db.execute(
            text(
                "DROP TRIGGER fail_daily_progress ON aggregate_job_progress; "
                "DROP FUNCTION fail_daily_progress()"
            )
        )
        await db.commit()


async def seed_day(factory: SessionFactory, project: UUID) -> None:
    started = midnight(today() - timedelta(days=7)) + timedelta(hours=12)
    async with factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "original": TraceSeed(
                    started, (SpanSeed("llm", started, Decimal("1.25"), 10, 5),), name="source"
                )
            },
        )
        await db.commit()
