"""The supported CLI must settle an exact durable prefix, including empty hours."""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bind_project
from app.rollups.compute import floor_hour
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.recovery import resume_recent
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at, run_recent, state


async def cli_recover(
    url: str, project: object, start: datetime, end: datetime, *extra: str, resume: bool = True
) -> tuple[int, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("_API_KEY") and k != "TEST_DATABASE_URL"
    }
    env.update(DATABASE_URL=url, SENTRY_DSN="")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli",
        "rollups",
        "backfill",
        "--project",
        str(project),
        "--from",
        start.isoformat(),
        "--to",
        end.isoformat(),
        *(["--resume-recent"] if resume else []),
        *extra,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    return proc.returncode or 0, output.decode()


async def test_supported_cli_recovers_missed_and_complete_empty_hours(
    session_factory: SessionFactory, app_database_url: str
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 7, started_at=start)
    code, output = await cli_recover(app_database_url, project, start, start + timedelta(hours=6))
    assert code == 0, output
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=6)
    assert await fetch(
        session_factory,
        "SELECT sum(span_count) AS n FROM span_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"n": 7}]


async def test_retention_shrink_expand_does_not_skip_cursor_and_stale_is_not_replayed(
    session_factory: SessionFactory, app_database_url: str
) -> None:

    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(days=40)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 3, started_at=start + timedelta(hours=3))
    code, output = await cli_recover(app_database_url, project, start, start + timedelta(hours=3))
    assert code == 0, output
    cursor = start + timedelta(hours=3)
    await execute(
        session_factory,
        "INSERT INTO "
        "aggregate_dirty_hours(project_id,bucket_hour,generation,pending_consumers) "
        "VALUES(:p,:h,42,8)",
        {"p": project, "h": cursor},
    )
    dirty = await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours")
    await execute(
        session_factory, "UPDATE projects SET retention_days=1 WHERE id=:p", {"p": project}
    )
    assert (await state(session_factory, project))["next_closed_hour"] == cursor
    async with session_factory() as db:
        await bind_project(db, project)
        complete = CoverageState()
        pending = CoverageState()
        await read_covered(
            db, text("SELECT 1"), {}, project, start, cursor, complete, relevant_consumers=1
        )
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            cursor,
            cursor + timedelta(hours=1),
            pending,
            relevant_consumers=1,
        )
        assert complete.status == "complete" and pending.status == "pending"
    await execute(
        session_factory, "UPDATE projects SET retention_days=90 WHERE id=:p", {"p": project}
    )
    assert (await state(session_factory, project))["next_closed_hour"] == cursor
    code, output = await cli_recover(app_database_url, project, cursor, cursor + timedelta(hours=3))
    assert code == 0, output
    assert (await state(session_factory, project))["next_closed_hour"] == cursor + timedelta(
        hours=3
    )
    assert (await state(session_factory, project))["closed_pending_since"] == start + timedelta(
        hours=1, minutes=10
    )
    assert await fetch(session_factory, "SELECT * FROM aggregate_dirty_hours") == dirty
    assert await fetch(session_factory, "SELECT sum(span_count) n FROM span_rollups_hourly") == [
        {"n": 3}
    ]
    after = await state(session_factory, project)
    code, output = await cli_recover(app_database_url, project, cursor, cursor + timedelta(hours=3))
    assert code == 0 and "stale; resume at current cursor" in output
    assert await state(session_factory, project) == after


@pytest.mark.parametrize("mode", ["island", "fraction", "offset", "future", "too_long"])
async def test_recovery_rejects_invalid_ranges_without_mutation(
    session_factory: SessionFactory, app_database_url: str, mode: str
) -> None:

    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(days=100)
    await admit_at(session_factory, project, start)
    end = start + timedelta(hours=3)
    if mode == "island":
        start += timedelta(hours=1)
    elif mode == "fraction":
        start += timedelta(seconds=1)
    elif mode == "offset":
        start = start.astimezone(timezone(timedelta(hours=2)))
    elif mode == "future":
        start = (await state(session_factory, project))["next_closed_hour"]
        end = datetime.now(UTC) + timedelta(days=1)
    else:
        end = start + timedelta(days=91)
    before = await state(session_factory, project)
    code, output = await cli_recover(app_database_url, project, start, end)
    assert code != 0, output
    assert await state(session_factory, project) == before


async def test_recovery_fault_preserves_committed_chunk_and_reports_exact_resume(
    session_factory: SessionFactory, app_database_url: str
) -> None:

    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await execute(
        session_factory,
        """CREATE FUNCTION interrupt_recent() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.next_closed_hour > NEW.admitted_hour+interval '3 hours' THEN
      RAISE EXCEPTION 'injected interruption'; END IF; RETURN NEW; END $$;
      CREATE TRIGGER interrupt_recent BEFORE UPDATE ON aggregate_job_progress
      FOR EACH ROW EXECUTE FUNCTION interrupt_recent()""",
    )
    try:
        code, output = await cli_recover(
            app_database_url, project, start, start + timedelta(hours=9)
        )
        assert code == 0 and "incomplete" in output, output
        assert (start + timedelta(hours=3)).isoformat() in output
        assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(
            hours=3
        )
    finally:
        await execute(
            session_factory,
            "DROP TRIGGER interrupt_recent ON aggregate_job_progress; DROP FUNCTION "
            "interrupt_recent()",
        )
    code, output = await cli_recover(
        app_database_url, project, start + timedelta(hours=3), start + timedelta(hours=9)
    )
    assert code == 0, output
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=9)


async def test_recovery_cap_returns_durable_resume_after_hundred_empty_chunks(
    session_factory: SessionFactory,
    app_database_url: str,
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(days=20)
    await admit_at(session_factory, project, start)
    code, output = await cli_recover(app_database_url, project, start, start + timedelta(days=15))
    assert code == 0 and "incomplete" in output and "chunks 100;" in output, output
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(
        hours=300
    )
    assert await fetch(session_factory, "SELECT * FROM span_rollups_hourly") == []


async def test_worker_advances_between_validation_and_source_lock_returns_stale_cursor(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    async with session_factory() as holder, session_factory() as recovery_db:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{project}"},
        )
        worker = asyncio.create_task(run_recent(session_factory, settings, now))
        async with asyncio.timeout(0.2):
            while (  # noqa: ASYNC110 - observe the actual PostgreSQL lock wait.
                await fetch(
                    session_factory,
                    "SELECT count(*) n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                )
            )[0]["n"] < 1:
                await asyncio.sleep(0.005)
        recovery = asyncio.create_task(
            resume_recent(recovery_db, project, start, start + timedelta(hours=9))
        )
        async with asyncio.timeout(0.2):
            while (  # noqa: ASYNC110 - observe the actual PostgreSQL lock wait.
                await fetch(
                    session_factory,
                    "SELECT count(*) n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                )
            )[0]["n"] < 2:
                await asyncio.sleep(0.005)
        await holder.commit()
        await worker
        result = await recovery
    assert result.status == "stale; resume at current cursor"
    assert result.chunks == 0 and result.cursor == start + timedelta(hours=3)
    assert (await state(session_factory, project))["next_closed_hour"] == result.cursor


async def test_default_manual_backfill_still_widens_hours_without_advancing_cursor(
    session_factory: SessionFactory,
    app_database_url: str,
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 7, started_at=start)
    before = await state(session_factory, project)
    code, output = await cli_recover(
        app_database_url,
        project,
        start + timedelta(minutes=30),
        start + timedelta(minutes=45),
        resume=False,
    )
    assert code == 0 and "Wrote 2 rollup rows." in output, output
    assert await state(session_factory, project) == before
    assert await fetch(session_factory, "SELECT sum(span_count) n FROM span_rollups_hourly") == [
        {"n": 7}
    ]
