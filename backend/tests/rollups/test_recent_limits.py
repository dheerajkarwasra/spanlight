"""Real PostgreSQL delays calibrate one invocation budget and durable candidate limits."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.jobs.context import TaskContext
from app.jobs.tasks import TASKS
from app.rollups.compute import floor_hour
from tests.attribution.daily_support import queued
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at, run_recent, state

pytestmark = pytest.mark.serial


async def test_candidate_cap_survives_restart_and_serves_idle_tenants(
    session_factory: SessionFactory, settings: Settings
) -> None:
    projects = sorted(await new_projects(session_factory, 105))
    await seed_llm_spans(session_factory, projects[-1], 2, started_at=datetime.now(UTC))
    started = monotonic()
    await run_recent(session_factory, settings)
    first = monotonic() - started
    rows = await fetch(
        session_factory,
        "SELECT attempt_order FROM aggregate_job_progress WHERE kind='rollup_recent'",
    )
    assert sum(row["attempt_order"] > 0 for row in rows) == 100
    assert (
        await fetch(
            session_factory,
            "SELECT * FROM span_rollups_hourly WHERE project_id=:p",
            {"p": projects[-1]},
        )
        == []
    )
    await run_recent(session_factory, settings)
    assert (await state(session_factory, projects[-1]))["attempt_order"] > 0
    assert await fetch(
        session_factory,
        "SELECT sum(span_count) n FROM span_rollups_hourly WHERE project_id=:p",
        {"p": projects[-1]},
    ) == [{"n": 2}]
    print("CALIBRATION recent_candidates=100 first_seconds=", first)


async def test_discovery_deadline_does_not_commit_attempt(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory,
        """CREATE FUNCTION recent_slow_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.attempt_order<>OLD.attempt_order THEN PERFORM pg_sleep(2); END IF;
      RETURN NEW; END $$; CREATE TRIGGER recent_slow_attempt BEFORE UPDATE ON aggregate_job_progress
      FOR EACH ROW EXECUTE FUNCTION recent_slow_attempt()""",
    )
    try:
        started = monotonic()
        await run_recent(session_factory, settings)
        elapsed = monotonic() - started
        assert (await state(session_factory, project))["attempt_order"] == 0
        assert 0.8 <= elapsed < 2
        print("CALIBRATION recent_discovery_seconds=", elapsed)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER recent_slow_attempt ON aggregate_job_progress; DROP FUNCTION "
            "recent_slow_attempt()",
        )


async def test_heartbeat_lock_starts_no_candidate(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    job = await queued(session_factory, "rollup_hourly", {})
    async with session_factory() as holder:
        await holder.execute(text("SELECT * FROM jobs WHERE id=:id FOR UPDATE"), {"id": job.id})
        started = monotonic()
        with pytest.raises((DBAPIError, TimeoutError)):
            await TASKS[job.kind](TaskContext(job, settings, session_factory), {})
        elapsed = monotonic() - started
    assert (await state(session_factory, project))["attempt_order"] == 0
    assert 0.2 <= elapsed < 1.5
    print("CALIBRATION recent_heartbeat_seconds=", elapsed)


async def test_slow_domain_rolls_back_and_healthy_tenant_completes(
    session_factory: SessionFactory, settings: Settings
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, first, 1, started_at=now)
    await seed_llm_spans(session_factory, second, 2, started_at=now)
    before = await state(session_factory, first)
    await execute(
        session_factory,
        """CREATE FUNCTION slow_recent_cursor() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.kind='rollup_recent' AND NEW.next_closed_hour<>OLD.next_closed_hour
      AND NEW.project_id='"""
        + str(first)
        + """'::uuid THEN PERFORM pg_sleep(10); END IF;
      RETURN NEW; END $$; CREATE TRIGGER slow_recent_cursor BEFORE UPDATE ON aggregate_job_progress
      FOR EACH ROW EXECUTE FUNCTION slow_recent_cursor()""",
    )
    try:
        started = monotonic()
        await run_recent(session_factory, settings, now)
        elapsed = monotonic() - started
        assert 4.8 <= elapsed < 7
        assert (await state(session_factory, first))["next_closed_hour"] == before[
            "next_closed_hour"
        ]
        assert (await state(session_factory, first))["attempt_order"] > before["attempt_order"]
        assert await fetch(
            session_factory,
            "SELECT project_id,sum(span_count) n FROM span_rollups_hourly GROUP BY 1",
        ) == [{"project_id": second, "n": 2}]
        print("CALIBRATION recent_domain_rollback_seconds=", elapsed)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER slow_recent_cursor ON aggregate_job_progress; DROP FUNCTION "
            "slow_recent_cursor()",
        )


async def test_recent_and_maintenance_share_one_start_deadline(
    session_factory: SessionFactory, settings: Settings
) -> None:
    projects = await new_projects(session_factory, 100)
    now = datetime.now(UTC)
    for project in projects:
        await seed_llm_spans(session_factory, project, 1, started_at=now)
        await seed_llm_spans(session_factory, project, 1, started_at=now - timedelta(hours=6))
    await execute(
        session_factory,
        """CREATE TABLE recent_start_probe(project_id uuid,bucket timestamptz,started timestamptz);
      CREATE FUNCTION slow_recent_product() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      INSERT INTO recent_start_probe
      VALUES(NEW.project_id,NEW.bucket_start,transaction_timestamp());
      PERFORM pg_sleep(.55); RETURN NEW; END $$;
      CREATE TRIGGER slow_recent_product BEFORE INSERT ON span_rollups_hourly
      FOR EACH ROW EXECUTE FUNCTION slow_recent_product()""",
    )
    entries = []
    frames = []
    handler_frame = None

    def observe(frame, event, arg):
        nonlocal handler_frame
        if event != "call" or frame in frames:
            return
        if frame.f_code.co_name == "run_recent" and "app/rollups" in frame.f_code.co_filename:
            handler_frame = frame
            frames.append(frame)
        if frame.f_code.co_name in {"next_candidate", "run_one_project"}:
            frames.append(frame)
            if handler_frame is not None:
                entries.append(
                    (
                        frame.f_code.co_name,
                        asyncio.get_running_loop().time() - handler_frame.f_locals["started"],
                    )
                )

    previous = sys.getprofile()
    try:
        [clock] = await fetch(session_factory, "SELECT clock_timestamp() started")
        started = monotonic()
        sys.setprofile(observe)
        await run_recent(session_factory, settings, now)
        sys.setprofile(previous)
        elapsed = monotonic() - started
        probes = await fetch(session_factory, "SELECT * FROM recent_start_probe ORDER BY started")
        assert 35 < len(probes) < 100
        assert (probes[-1]["started"] - clock["started"]).total_seconds() < 30.2
        assert 30 <= elapsed < 33
        assert entries and all(age < 30 for _, age in entries)
        assert all(age < 20 for kind, age in entries if kind == "next_candidate")
        rows = await fetch(
            session_factory,
            "SELECT count(*) n FROM aggregate_job_progress WHERE kind='rollup_recent' AND "
            "attempt_order>0",
        )
        assert 20 < rows[0]["n"] < 50
        print("MONOTONIC_STARTS", entries)
        print(
            "CALIBRATION shared_seconds=",
            elapsed,
            "committed_products=",
            len(probes),
            "attempted=",
            rows[0]["n"],
        )
    finally:
        sys.setprofile(previous)
        await execute(
            session_factory,
            "DROP TRIGGER slow_recent_product ON span_rollups_hourly; DROP FUNCTION "
            "slow_recent_product(); DROP TABLE recent_start_probe",
        )


@pytest.mark.parametrize("per_hour", [1000, 10000])
async def test_six_disjoint_hour_capacity(
    session_factory: SessionFactory, settings: Settings, per_hour: int
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    old = floor_hour(now) - timedelta(days=3)
    await admit_at(session_factory, project, old)
    for hour in (
        old,
        old + timedelta(hours=1),
        old + timedelta(hours=2),
        floor_hour(now) - timedelta(hours=2),
        floor_hour(now) - timedelta(hours=1),
        floor_hour(now),
    ):
        await seed_llm_spans(session_factory, project, per_hour, started_at=hour)
    await execute(
        session_factory,
        "UPDATE spans SET model='model-' || (abs(hashtext(trace_id)::bigint)%1000)::text, "
        "status='error',error_class='timeout' WHERE project_id=:p",
        {"p": project},
    )
    started = monotonic()
    await run_recent(session_factory, settings, now)
    elapsed = monotonic() - started
    assert (await state(session_factory, project))["next_closed_hour"] == old + timedelta(hours=3)
    assert await fetch(
        session_factory,
        "SELECT count(DISTINCT bucket_start) hours,sum(span_count) spans FROM "
        "span_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"hours": 6, "spans": 6 * per_hour}]
    assert await fetch(
        session_factory,
        "SELECT sum(traces) traces FROM trace_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"traces": 6 * per_hour}]
    errors = await fetch(
        session_factory,
        "SELECT sum(errors) n,count(*) groups FROM error_rollups_hourly WHERE project_id=:p",
        {"p": project},
    )
    assert errors[0]["n"] == 6 * per_hour
    assert errors[0]["groups"] > 3000
    print(
        "CALIBRATION six_disjoint_hours total_source=",
        6 * per_hour,
        "error_groups=",
        errors[0]["groups"],
        "seconds=",
        elapsed,
    )


async def test_score_repair_timeout_keeps_prior_rollups_and_does_not_commit_score_flag(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    await execute(
        session_factory,
        """INSERT INTO scores(id,project_id,trace_id,name,data_type,source,value)
      SELECT gen_random_uuid(),project_id,trace_id,'quality','boolean','api',1
      FROM traces WHERE project_id=:p""",
        {"p": project},
    )
    await execute(
        session_factory,
        """CREATE FUNCTION slow_score_repair() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.has_scores AND NOT OLD.has_scores THEN PERFORM pg_sleep(10); END IF;
      RETURN NEW; END $$; CREATE TRIGGER slow_score_repair BEFORE UPDATE ON traces
      FOR EACH ROW EXECUTE FUNCTION slow_score_repair()""",
    )
    try:
        started = monotonic()
        await run_recent(session_factory, settings, now)
        elapsed = monotonic() - started
        assert 4.8 <= elapsed < 7
        assert await fetch(
            session_factory, "SELECT sum(span_count) n FROM span_rollups_hourly"
        ) == [{"n": 1}]
        assert await fetch(session_factory, "SELECT has_scores FROM traces") == [
            {"has_scores": False}
        ]
        print("CALIBRATION score_timeout_seconds=", elapsed)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER slow_score_repair ON traces; DROP FUNCTION slow_score_repair()",
        )
