"""Daily work preserves admission, flags, snapshots, and atomic continuation."""

import asyncio
import logging
from datetime import timedelta

import pytest

from app.config import Settings
from app.jobs.context import TaskContext
from app.jobs.queue import LeaseLostError, complete
from app.jobs.tasks import TASKS
from tests.attribution.daily_support import (
    child,
    dispatch,
    fail_before_cursor,
    queued,
    remove_cursor_failure,
    run,
    scheduled_rows,
    seed_day,
    state,
    today,
)
from tests.gateway_support import logged
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects


@pytest.fixture
def enabled(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "attribution_enabled": True,
            "user_stats_enabled": False,
            "aggregate_repair_enabled": False,
        }
    )


async def test_six_empty_days_finish_with_refreshed_outcome_across_restarts(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    admitted = await state(session_factory, project)
    for offset in range(6):
        await dispatch(session_factory, enabled)
        job = await child(session_factory)
        assert job.payload["day"] == (today() - timedelta(days=7 - offset)).isoformat()
        await run(session_factory, enabled, job)
    settled = await state(session_factory, project)
    assert settled["first_period"] == admitted["first_period"] == today()
    assert settled["settled_period"] == today()
    assert settled["settled_outcomes"] == 1
    assert settled["active_period"] is None and settled["next_day"] is None
    assert settled["active_outcomes"] == 0 and settled["queued_key"] is None
    assert await fetch(session_factory, "SELECT * FROM attribution_rollups_daily") == []
    assert await fetch(session_factory, "SELECT * FROM user_stats_daily") == []


async def test_reverse_horizons_and_replayed_child_never_rewind(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled, today() + timedelta(days=1))
    first = await child(session_factory)
    assert first.payload["scheduled_period"] == today().isoformat()
    await dispatch(session_factory, enabled, today())
    assert len(await scheduled_rows(session_factory)) == 1
    await run(session_factory, enabled, first, finish=False)
    durable = await state(session_factory, project)
    await TASKS[first.kind](TaskContext(first, enabled, session_factory), first.payload)
    assert await state(session_factory, project) == durable
    async with session_factory() as db:
        await complete(db, first)
    await dispatch(session_factory, enabled, today() - timedelta(days=1))
    current = await state(session_factory, project)
    assert current["next_day"] == durable["next_day"]
    assert current["dispatch_number"] == durable["dispatch_number"]


async def test_disabled_child_preserves_day_and_reenable_replaces_terminal_key(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    first = await child(session_factory)
    before = await state(session_factory, project)
    disabled = enabled.model_copy(update={"attribution_enabled": False})
    await run(session_factory, disabled, first)
    assert await state(session_factory, project) == before
    await dispatch(session_factory, disabled)
    assert await state(session_factory, project) == before
    await dispatch(session_factory, enabled)
    replacement = await child(session_factory)
    assert replacement.payload["day"] == first.payload["day"]
    assert replacement.payload["dispatch_number"] == first.payload["dispatch_number"] + 1
    await run(session_factory, enabled, replacement)
    assert (await state(session_factory, project))["next_day"] == today() - timedelta(days=6)


async def test_failure_before_cursor_rolls_back_then_retry_settles_once(
    session_factory: SessionFactory,
    enabled: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await seed_day(session_factory, project)
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    before = await state(session_factory, project)
    await fail_before_cursor(session_factory)
    # The expected failure is asserted here instead of printing its traceback into the run's log.
    project_pass = logging.getLogger("app.jobs.project_pass")
    project_pass.addHandler(caplog.handler)
    project_pass.propagate = False
    try:
        with pytest.raises(RuntimeError, match="did not complete"):
            await run(session_factory, enabled, job)
        [failed] = logged(caplog, "attribution_daily_project_failed")
        assert failed["project_id"] == str(project)
        assert failed["level"] == "error"
        assert await state(session_factory, project) == before
        assert await fetch(session_factory, "SELECT * FROM attribution_rollups_daily") == []
    finally:
        project_pass.propagate = True
        project_pass.removeHandler(caplog.handler)
        await remove_cursor_failure(session_factory)
    await run(session_factory, enabled, job)
    assert (await state(session_factory, project))["next_day"] == today() - timedelta(days=6)


async def test_concurrent_duplicate_children_advance_one_day(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    first = await child(session_factory)
    duplicate = await queued(session_factory, first.kind, first.payload)
    await asyncio.gather(
        run(session_factory, enabled, first), run(session_factory, enabled, duplicate)
    )
    assert (await state(session_factory, project))["next_day"] == today() - timedelta(days=6)


async def test_unavailable_day_preserves_snapshot_and_dirty_generation(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory,
        "UPDATE projects SET aggregate_history_unverified_before=NULL WHERE id=:p",
        {"p": project},
    )
    await execute(
        session_factory,
        "INSERT INTO aggregate_dirty_hours VALUES (:p,date_trunc('hour',now()),123,now(),15)",
        {"p": project},
    )
    dirty = await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours")
    await dispatch(session_factory, enabled)
    await run(session_factory, enabled, await child(session_factory))
    assert (await state(session_factory, project))["active_outcomes"] == 2
    assert await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours") == dirty


async def test_idle_expiry_is_arithmetic_and_later_retention_does_not_resurrect(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory, "UPDATE projects SET retention_days=1 WHERE id=:p", {"p": project}
    )
    await dispatch(session_factory, enabled)
    expired = await state(session_factory, project)
    assert expired["settled_period"] == today() and expired["settled_outcomes"] == 4
    assert expired["expired_through_period"] == today()
    assert await scheduled_rows(session_factory) == []
    await execute(
        session_factory, "UPDATE projects SET retention_days=90 WHERE id=:p", {"p": project}
    )
    await dispatch(session_factory, enabled)
    assert (await state(session_factory, project))["settled_period"] == today()
    assert await scheduled_rows(session_factory) == []


async def test_deleted_project_child_is_harmless_and_cascades_progress(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    await execute(session_factory, "DELETE FROM projects WHERE id=:p", {"p": project})
    await run(session_factory, enabled, job)
    assert await fetch(session_factory, "SELECT * FROM aggregate_job_progress") == []


@pytest.mark.parametrize(
    "change", [{"day": "2026-01-01"}, {"dispatch_number": True}, {"first_day": "bad"}]
)
async def test_malformed_payload_does_not_touch_progress(
    session_factory: SessionFactory,
    enabled: Settings,
    change: dict,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    before = await state(session_factory, project)
    with pytest.raises(ValueError, match=r"dates|dispatch_number|Invalid isoformat"):
        await TASKS[job.kind](TaskContext(job, enabled, session_factory), job.payload | change)
    assert await state(session_factory, project) == before


async def test_reclaimed_fence_propagates_and_cannot_advance(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    before = await state(session_factory, project)
    await execute(session_factory, "UPDATE jobs SET fence=fence+1 WHERE id=:id", {"id": job.id})
    with pytest.raises(LeaseLostError):
        await run(session_factory, enabled, job)
    assert await state(session_factory, project) == before


async def test_child_from_two_days_ago_cannot_rewind_the_cursor(
    session_factory: SessionFactory,
    enabled: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await dispatch(session_factory, enabled)
    first = await child(session_factory)
    await run(session_factory, enabled, first)
    await dispatch(session_factory, enabled)
    await run(session_factory, enabled, await child(session_factory))
    before = await state(session_factory, project)
    replay = await queued(session_factory, first.kind, first.payload)
    await run(session_factory, enabled, replay)
    assert before["next_day"] == today() - timedelta(days=5)
    assert await state(session_factory, project) == before
