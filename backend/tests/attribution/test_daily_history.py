"""Historical fixture construction and durable recovery of expired/reclaimed daily work."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bypass_rls
from app.jobs.queue import LeaseLostError, claim_next
from tests.attribution.daily_support import child, dispatch, run, seed_day, state, today
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects


async def historical_admission(factory: SessionFactory, project: Any) -> None:
    """Only fixture setup: PG wall time cannot move backwards40days.

    One schema-owner transaction, no concurrent writers. Both admission kinds
    shift together; no evidence is removed. Genuine admission tests never do this.
    """
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
            first_period=first_period-40,settled_period=settled_period-40,
            admitted_hour=admitted_hour-interval '40 days',
            next_closed_hour=next_closed_hour-interval '40 days'
            WHERE project_id=:project"""),
            {"project": project},
        )
        await db.execute(
            text(
                "ALTER TABLE aggregate_job_progress ENABLE TRIGGER aggregate_job_progress_immutable"
            )
        )
        await db.commit()


async def test_disabled_forty_day_backlog_expires_contiguous_idle_range_without_reading_raw(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await historical_admission(session_factory, project)
    before = await state(session_factory, project)
    await dispatch(session_factory, settings.model_copy(update={"attribution_enabled": False}))
    assert await state(session_factory, project) == before
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    after = await state(session_factory, project)
    assert after["first_period"] == today() - timedelta(days=40)
    assert after["settled_period"] == today() - timedelta(days=29)
    assert after["expired_through_period"] == today() - timedelta(days=29)
    assert after["settled_outcomes"] == 4
    assert after["active_period"] == today() - timedelta(days=28)
    assert after["next_day"] == today() - timedelta(days=35)
    await run(session_factory, enabled, await child(session_factory))
    assert (await state(session_factory, project))["active_outcomes"] == 4
    assert await fetch(session_factory, "SELECT * FROM aggregate_incomplete_hours") == []


async def test_active_old_period_keeps_its_days_through_disable_and_retention_shrink(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await historical_admission(session_factory, project)
    await execute(
        session_factory, "UPDATE projects SET retention_days=90 WHERE id=:p", {"p": project}
    )
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    first = await child(session_factory)
    await run(session_factory, enabled.model_copy(update={"attribution_enabled": False}), first)
    before = await state(session_factory, project)
    await execute(
        session_factory, "UPDATE projects SET retention_days=30 WHERE id=:p", {"p": project}
    )
    await dispatch(session_factory, enabled)
    replacement = await child(session_factory)
    assert replacement.payload["day"] == first.payload["day"]
    assert replacement.payload["scheduled_period"] == first.payload["scheduled_period"]
    await run(session_factory, enabled, replacement)
    after = await state(session_factory, project)
    assert after["next_day"] == before["next_day"] + timedelta(days=1)
    assert after["active_outcomes"] == 4 and after["active_period"] == before["active_period"]


@pytest.mark.parametrize("remove_job", [False, True])
async def test_exhausted_or_deleted_child_is_replaced_with_fresh_key(
    session_factory: SessionFactory,
    settings: Settings,
    remove_job: bool,
) -> None:
    [project] = await new_projects(session_factory, 1)
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    first = await child(session_factory)
    await execute(
        session_factory,
        "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 minute' WHERE id=:id",
        {"id": first.id},
    )
    async with session_factory() as db:
        assert await claim_next(db, supported_kinds={first.kind}) is None
    if remove_job:
        await execute(session_factory, "DELETE FROM jobs WHERE id=:id", {"id": first.id})
    await dispatch(session_factory, enabled)
    second = await child(session_factory)
    assert second.payload["day"] == first.payload["day"]
    assert second.payload["dispatch_number"] == first.payload["dispatch_number"] + 1
    await run(session_factory, enabled, second)
    assert (await state(session_factory, project))["next_day"] == today() - timedelta(days=6)


async def test_actual_reclaim_during_source_wait_rolls_back_day_and_propagates_lease_loss(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    before = await state(session_factory, project)
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{project}"},
        )
        pending = asyncio.create_task(run(session_factory, enabled, job))
        async with asyncio.timeout(0.2):
            while not (  # noqa: ASYNC110 - observe an actual PostgreSQL lock wait, not a task event.
                await fetch(
                    session_factory,
                    "SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                )
            )[0]["n"]:
                await asyncio.sleep(0.005)
        await execute(session_factory, "UPDATE jobs SET fence=fence+1 WHERE id=:id", {"id": job.id})
        await holder.commit()
        with pytest.raises(LeaseLostError, match="live commit lease"):
            await pending
    assert await state(session_factory, project) == before


@pytest.mark.parametrize("unavailable", [False, True])
async def test_partial_retention_day_is_refreshed_or_unavailable_never_expired(
    session_factory: SessionFactory,
    settings: Settings,
    unavailable: bool,
) -> None:

    [project] = await new_projects(session_factory, 1)
    await seed_day(session_factory, project)
    await execute(
        session_factory, "UPDATE projects SET retention_days=7 WHERE id=:p", {"p": project}
    )
    if unavailable:
        await execute(
            session_factory,
            "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
            "VALUES(:p,date_trunc('day',now())-interval '7 days','retention')",
            {"p": project},
        )
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    await run(session_factory, enabled, await child(session_factory))
    current = await state(session_factory, project)
    assert current["active_outcomes"] == (2 if unavailable else 1)
    rows = await fetch(
        session_factory,
        "SELECT cost_usd FROM attribution_rollups_daily WHERE group_kind='trace_name'",
    )
    assert rows == ([] if unavailable else [{"cost_usd": Decimal("1.25")}])
