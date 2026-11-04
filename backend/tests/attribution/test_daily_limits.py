"""Real PostgreSQL lock, discovery and candidate-cap calibration."""

import asyncio
import sys
from time import monotonic

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.rls import bind_project
from app.jobs.context import TaskContext
from app.jobs.tasks import TASKS
from tests.attribution.daily_support import child, dispatch, queued, run, seed_day, state, today
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects

pytestmark = pytest.mark.serial


async def test_hundred_candidate_cap_persists_order_and_next_invocation_reaches_tail(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    projects = await new_projects(session_factory, 105)
    enabled = settings.model_copy(update={"attribution_enabled": True})
    started = monotonic()
    await dispatch(session_factory, enabled)
    elapsed = monotonic() - started
    rows = await fetch(
        session_factory,
        "SELECT attempt_order FROM aggregate_job_progress WHERE kind='attribution_daily'",
    )
    assert sum(row["attempt_order"] > 0 for row in rows) == 100
    print("CALIBRATION candidates=100 domain_transactions=100 seconds=", elapsed)
    await dispatch(session_factory, enabled)
    rows = await fetch(
        session_factory,
        "SELECT attempt_order FROM aggregate_job_progress WHERE kind='attribution_daily'",
    )
    assert all(row["attempt_order"] > 0 for row in rows)
    assert len(projects) == 105 and elapsed < 40


async def test_source_locked_first_candidate_does_not_starve_the_next(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    enabled = settings.model_copy(update={"attribution_enabled": True})
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{first}"},
        )
        started = monotonic()
        await dispatch(session_factory, enabled)
        elapsed = monotonic() - started
    blocked = await state(session_factory, first)
    healthy = await state(session_factory, second)
    assert blocked["attempt_order"] > 0 and blocked["active_period"] is None
    assert (
        healthy["attempt_order"] > blocked["attempt_order"] and healthy["active_period"] == today()
    )
    assert 0.2 <= elapsed < 2
    print("CALIBRATION source_lock_seconds=", elapsed)


async def test_locked_progress_is_skipped_without_claiming_an_attempt(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    async with session_factory() as holder:
        await bind_project(holder, first)
        await holder.execute(
            text("SELECT * FROM aggregate_job_progress WHERE kind='attribution_daily' FOR UPDATE")
        )
        await dispatch(session_factory, settings.model_copy(update={"attribution_enabled": True}))
    assert (await state(session_factory, first))["attempt_order"] == 0
    assert (await state(session_factory, second))["attempt_order"] > 0


async def test_real_slow_discovery_rolls_back_attempt_and_ends_within_its_budget(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory,
        """CREATE FUNCTION slow_discovery() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.attempt_order<>OLD.attempt_order THEN PERFORM pg_sleep(2); END IF;
          RETURN NEW; END $$;
        CREATE TRIGGER slow_discovery BEFORE UPDATE ON aggregate_job_progress
        FOR EACH ROW EXECUTE FUNCTION slow_discovery()""",
    )
    try:
        started = monotonic()
        await dispatch(session_factory, settings.model_copy(update={"attribution_enabled": True}))
        elapsed = monotonic() - started
        assert 0.8 <= elapsed < 2
        assert (await state(session_factory, project))["attempt_order"] == 0
        print("CALIBRATION discovery_timeout_seconds=", elapsed)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER slow_discovery ON aggregate_job_progress; DROP FUNCTION slow_discovery()",
        )


async def test_delayed_heartbeat_starts_no_candidate(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    job = await queued(
        session_factory, "schedule_attribution_daily", {"scheduled_period": today().isoformat()}
    )
    async with session_factory() as holder:
        await holder.execute(text("SELECT * FROM jobs WHERE id=:id FOR UPDATE"), {"id": job.id})
        started = monotonic()

        with pytest.raises((DBAPIError, TimeoutError)):
            await TASKS[job.kind](
                TaskContext(
                    job, settings.model_copy(update={"attribution_enabled": True}), session_factory
                ),
                job.payload,
            )
        elapsed = monotonic() - started
    assert (await state(session_factory, project))["attempt_order"] == 0
    assert 0.2 <= elapsed < 1.5
    print("CALIBRATION heartbeat_timeout_seconds=", elapsed)


async def test_registered_dispatcher_stops_starting_work_at_one_shared_deadline(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    await new_projects(session_factory, 100)
    await execute(
        session_factory,
        """CREATE TABLE daily_start_probe(started timestamptz);
        CREATE FUNCTION slow_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.kind='refresh_attribution_daily' THEN
            INSERT INTO daily_start_probe VALUES(transaction_timestamp());
            PERFORM pg_sleep(0.55);
        END IF; RETURN NEW; END $$;
        CREATE TRIGGER slow_dispatch BEFORE INSERT ON jobs
        FOR EACH ROW EXECUTE FUNCTION slow_dispatch()""",
    )
    entries = []
    frames = []
    handler_frame = None

    def observe(frame, event, arg):
        nonlocal handler_frame
        if event != "call" or frame in frames:
            return
        if frame.f_code.co_name == "run_schedule_attribution_daily":
            handler_frame = frame
            frames.append(frame)
        if frame.f_code.co_name in {"next_candidate", "run_one_project"}:
            frames.append(frame)
            if handler_frame is not None:
                age = asyncio.get_running_loop().time() - handler_frame.f_locals["started"]
                entries.append((frame.f_code.co_name, age))

    previous_profile = sys.getprofile()
    try:
        [clock] = await fetch(session_factory, "SELECT clock_timestamp() AS started")
        started = monotonic()
        sys.setprofile(observe)
        await dispatch(session_factory, settings.model_copy(update={"attribution_enabled": True}))
        sys.setprofile(previous_profile)
        elapsed = monotonic() - started
        print("MONOTONIC_STARTS", entries)
        probes = await fetch(
            session_factory, "SELECT started FROM daily_start_probe ORDER BY started"
        )
        assert 20 < len(probes) < 100
        assert (probes[-1]["started"] - clock["started"]).total_seconds() < 30.15
        assert 30 <= elapsed < 32
        # Diagnostic profiler proof supplements the committed-state assertions above.
        assert entries and all(age < 30.0 for _, age in entries)
        print("CALIBRATION shared_deadline_seconds=", elapsed, "committed_children=", len(probes))
    finally:
        sys.setprofile(previous_profile)
        await execute(
            session_factory,
            "DROP TRIGGER slow_dispatch ON jobs; DROP FUNCTION slow_dispatch(); DROP "
            "TABLE daily_start_probe",
        )


async def test_slow_day_transaction_rolls_back_output_and_cursor_within_budget(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await seed_day(session_factory, project)
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    before = await state(session_factory, project)
    await execute(
        session_factory,
        """CREATE FUNCTION slow_day() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.next_day IS DISTINCT FROM OLD.next_day THEN PERFORM pg_sleep(10); END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER slow_day BEFORE UPDATE ON aggregate_job_progress
        FOR EACH ROW EXECUTE FUNCTION slow_day()""",
    )
    try:
        started = monotonic()
        with pytest.raises(RuntimeError, match="did not complete"):
            await run(session_factory, enabled, job)
        elapsed = monotonic() - started
        assert 4.8 <= elapsed < 6
        assert await state(session_factory, project) == before
        assert await fetch(session_factory, "SELECT * FROM attribution_rollups_daily") == []
        print("CALIBRATION day_rollback_seconds=", elapsed)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER slow_day ON aggregate_job_progress; DROP FUNCTION slow_day()",
        )
