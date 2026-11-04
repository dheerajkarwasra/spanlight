"""Scheduled deep attribution must survive delayed execution and admission order."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.jobs.queue import claim_next, complete, enqueue
from app.jobs.tasks import TASKS
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects


@pytest.mark.parametrize("delay", [timedelta(minutes=16), timedelta(days=1, minutes=16)])
@pytest.mark.parametrize("horizon_offset", [0, 1])
async def test_delayed_scheduled_period_refreshes_its_oldest_day(
    session_factory: SessionFactory, settings: Settings, delay: timedelta, horizon_offset: int
) -> None:
    period = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    first = period - timedelta(days=7)
    [project] = await new_projects(session_factory, 1)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "late": TraceSeed(
                    first + timedelta(hours=12),
                    (SpanSeed("llm", first + timedelta(hours=12), Decimal("1.25"), 10, 5),),
                    name="late-source",
                )
            },
        )
        await enqueue(
            db,
            "schedule_attribution_daily",
            {"scheduled_period": (period + timedelta(days=horizon_offset)).date().isoformat()},
        )
        await db.commit()
        job = await claim_next(db)
    assert job is not None
    context = TaskContext(
        job,
        settings.model_copy(update={"attribution_enabled": True}),
        session_factory,
        clock=lambda: period + delay,
    )
    await TASKS[job.kind](context, job.payload)
    async with session_factory() as db:
        await complete(db, job)
        child = await claim_next(db)
    assert child is not None and child.kind == "refresh_attribution_daily"
    assert child.payload["day"] == first.date().isoformat()
    await TASKS[child.kind](
        TaskContext(child, context.settings, session_factory, clock=lambda: period + delay),
        child.payload,
    )
    rows = await fetch(
        session_factory,
        "SELECT day, cost_usd FROM attribution_rollups_daily "
        "WHERE project_id=:project AND group_kind='trace_name'",
        {"project": project},
    )
    assert rows == [{"day": first.date(), "cost_usd": Decimal("1.25")}]


async def test_project_admission_exists_before_any_dispatch(
    session_factory: SessionFactory,
) -> None:
    await new_projects(session_factory, 1)
    async with session_factory() as db:
        relation = await db.scalar(text("SELECT to_regclass('public.aggregate_job_progress')"))
    assert relation is not None, "Projects require immutable admission before candidate visits"
