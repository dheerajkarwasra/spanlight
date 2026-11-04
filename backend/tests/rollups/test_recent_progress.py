"""Closed-hour progress is atomic, source-aware and independent of retention/dirty repair."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from structlog.testing import capture_logs

from app.config import Settings
from app.db.rls import bind_project
from app.jobs.context import TaskContext
from app.jobs.queue import LeaseLostError
from app.jobs.tasks import TASKS
from app.rollups.compute import compute_rollups, floor_hour
from tests.attribution.daily_support import queued
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at, run_recent, state


@pytest.mark.parametrize("gap", [5, 51])
async def test_restarts_settle_contiguous_missed_hours_with_repair_disabled(
    session_factory: SessionFactory, settings: Settings, gap: int
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=gap)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 7, started_at=start, cost_usd=Decimal("1.25"))
    await execute(
        session_factory,
        "INSERT INTO "
        "aggregate_dirty_hours(project_id,bucket_hour,generation,first_marked_at,pending_consumers)"
        " VALUES(:p,:h,42,:h,15)",
        {"p": project, "h": start},
    )
    dirty = await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours")
    await run_recent(
        session_factory, settings.model_copy(update={"aggregate_repair_enabled": False}), now
    )
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=3)
    await run_recent(session_factory, settings, now + timedelta(hours=1))
    assert (await state(session_factory, project))["next_closed_hour"] == min(
        start + timedelta(hours=6), floor_hour(now + timedelta(minutes=50))
    )
    assert await fetch(
        session_factory,
        "SELECT span_count,cost_usd FROM span_rollups_hourly WHERE project_id=:p AND "
        "bucket_start=:h",
        {"p": project, "h": start},
    ) == [{"span_count": 7, "cost_usd": Decimal("8.75")}]
    assert await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours") == dirty


async def test_complete_empty_closure_removes_stale_group_without_fake_rows(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=8)
    await admit_at(session_factory, project, start)
    await execute(
        session_factory,
        "INSERT INTO "
        "span_rollups_hourly(project_id,bucket_start,kind,span_count,errors,input_tokens,output_tokens,cached_tokens,unpriced_calls,latency_buckets,ttft_buckets)"
        " VALUES(:p,:h,'llm',9,0,0,0,0,0,array_fill(0,ARRAY[32]),array_fill(0,ARRAY[32]))",
        {"p": project, "h": start},
    )
    await run_recent(session_factory, settings, now)
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=3)
    assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == []
    assert await fetch(session_factory, "SELECT * FROM trace_rollups_hourly") == []
    assert await fetch(session_factory, "SELECT * FROM error_rollups_hourly") == []


@pytest.mark.parametrize("legacy", [False, True])
async def test_unavailable_middle_and_legacy_sources_preserve_snapshots(
    session_factory: SessionFactory, settings: Settings, legacy: bool
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=8)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 2, started_at=start + timedelta(hours=1))
    async with session_factory() as db:
        await compute_rollups(db, project, start, start + timedelta(hours=3))
        await db.commit()
    before = await fetch(session_factory, "SELECT * FROM span_rollups_hourly")
    if legacy:
        await execute(
            session_factory,
            "UPDATE projects SET aggregate_history_unverified_before=NULL WHERE id=:p",
            {"p": project},
        )
    else:
        await execute(
            session_factory,
            "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
            "VALUES(:p,:h,'erasure')",
            {"p": project, "h": start + timedelta(hours=1)},
        )
    evidence = await fetch(session_factory, "SELECT * FROM aggregate_incomplete_hours")
    with capture_logs() as logs:
        await run_recent(session_factory, settings, now)
    [summary] = [log for log in logs if log["event"] == "rollups_recomputed"]
    # Legacy sources leave all three closed and three recent hours unverified.
    assert summary["unavailable_hours"] == (6 if legacy else 1)
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=3)
    assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == before
    assert await fetch(session_factory, "SELECT * FROM aggregate_incomplete_hours") == evidence


async def test_failure_after_span_write_rolls_back_all_products_and_cursor(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, project, 2, started_at=now)
    before = await state(session_factory, project)
    await execute(
        session_factory,
        """CREATE FUNCTION fail_trace_product() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
RAISE EXCEPTION 'injected second product failure'; END $$;
      CREATE TRIGGER
fail_trace_product BEFORE INSERT ON trace_rollups_hourly FOR EACH ROW EXECUTE
FUNCTION fail_trace_product()""",
    )
    try:
        await run_recent(session_factory, settings, now)
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER fail_trace_product ON trace_rollups_hourly; DROP FUNCTION "
            "fail_trace_product()",
        )
    after = await state(session_factory, project)
    assert after["attempt_order"] > before["attempt_order"]
    assert after["next_closed_hour"] == before["next_closed_hour"]
    assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == []
    assert await fetch(session_factory, "SELECT * FROM trace_rollups_hourly") == []
    assert await fetch(session_factory, "SELECT * FROM error_rollups_hourly") == []


async def test_fence_replacement_during_source_wait_rejects_domain_commit(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    before = await state(session_factory, project)
    job = await queued(session_factory, "rollup_hourly", {})
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{project}"},
        )
        task = asyncio.create_task(TASKS[job.kind](TaskContext(job, settings, session_factory), {}))
        async with asyncio.timeout(0.2):
            while not (  # noqa: ASYNC110 - observe the actual PostgreSQL lock wait.
                await fetch(
                    session_factory,
                    "SELECT count(*) n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                )
            )[0]["n"]:
                await asyncio.sleep(0.005)
        await execute(session_factory, "UPDATE jobs SET fence=fence+1 WHERE id=:id", {"id": job.id})
        await holder.commit()
        with pytest.raises(LeaseLostError):
            await task
    after = await state(session_factory, project)
    assert after["attempt_order"] > before["attempt_order"]
    assert after["next_closed_hour"] == before["next_closed_hour"]
    assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == []


async def test_progress_lock_skips_first_project_without_attempt_claim(
    session_factory: SessionFactory, settings: Settings
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    async with session_factory() as holder:
        await bind_project(holder, first)
        await holder.execute(
            text("SELECT * FROM aggregate_job_progress WHERE kind='rollup_recent' FOR UPDATE")
        )
        await run_recent(session_factory, settings)
    assert (await state(session_factory, first))["attempt_order"] == 0
    assert (await state(session_factory, second))["attempt_order"] > 0


async def test_cancellation_keeps_committed_attempt_but_rolls_back_inflight_outputs(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    before = await state(session_factory, project)
    await execute(
        session_factory,
        """CREATE FUNCTION pause_recent_flush() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.next_closed_hour<>OLD.next_closed_hour THEN
      PERFORM pg_advisory_xact_lock(7654321); END IF; RETURN NEW; END $$;
      CREATE TRIGGER pause_recent_flush BEFORE UPDATE ON aggregate_job_progress
      FOR EACH ROW EXECUTE FUNCTION pause_recent_flush()""",
    )
    try:
        async with session_factory() as holder:
            await holder.execute(text("SELECT pg_advisory_xact_lock(7654321)"))
            job = await queued(session_factory, "rollup_hourly", {})
            task = asyncio.create_task(
                TASKS[job.kind](TaskContext(job, settings, session_factory), {})
            )
            async with asyncio.timeout(0.2):
                while not (  # noqa: ASYNC110 - observe the actual PostgreSQL wait.
                    await fetch(
                        session_factory,
                        "SELECT count(*) n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                    )
                )[0]["n"]:
                    await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        after = await state(session_factory, project)
        assert after["attempt_order"] > before["attempt_order"]
        assert after["next_closed_hour"] == before["next_closed_hour"]
        assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == []
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER pause_recent_flush ON "
            "aggregate_job_progress; DROP FUNCTION "
            "pause_recent_flush()",
        )


async def test_concurrent_workers_serialize_contiguous_prefix(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 3, started_at=start)
    await asyncio.gather(
        run_recent(session_factory, settings, now), run_recent(session_factory, settings, now)
    )
    cursor = (await state(session_factory, project))["next_closed_hour"]
    assert start + timedelta(hours=3) <= cursor <= start + timedelta(hours=6)
    assert await fetch(session_factory, "SELECT sum(span_count) n FROM span_rollups_hourly") == [
        {"n": 3}
    ]


async def test_deletion_race_does_not_recreate_admission_or_outputs(
    session_factory: SessionFactory, settings: Settings
) -> None:
    first, second = sorted(await new_projects(session_factory, 2))
    now = datetime.now(UTC)
    await seed_llm_spans(session_factory, first, 1, started_at=now)
    await seed_llm_spans(session_factory, second, 2, started_at=now)
    async with session_factory() as holder:
        await bind_project(holder, first)
        await holder.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), {"p": first})
        await run_recent(session_factory, settings, now)
        await holder.execute(text("DELETE FROM projects WHERE id=:p"), {"p": first})
        await holder.commit()
    assert (
        await fetch(
            session_factory,
            "SELECT * FROM aggregate_job_progress WHERE project_id=:p",
            {"p": first},
        )
        == []
    )
    assert await fetch(
        session_factory, "SELECT project_id,sum(span_count) n FROM span_rollups_hourly GROUP BY 1"
    ) == [{"project_id": second, "n": 2}]
