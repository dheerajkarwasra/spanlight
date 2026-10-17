"""The billing fetch job at its edges: what happens after the commit, retries of a crashed
attempt, the job's own deadline, an adopted manual run, a broken payload (spec §10.2).

Failure modes: a run already `ok` (its lines committed) is relabelled `failed` because something
after the commit went wrong, so the report drops lines it should read; a retry after a crash
finds its own orphaned run and gives up; the fetch starts its 35 s clock late and the worker
kills the job mid-write; a slow queue lets another fetch close the manual run it is working
under; a cleanup failure hides why the job failed; a malformed payload is retried for nothing.
"""

import asyncio
import dataclasses
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.models import JobStatus
from app.db.models.billing import BillingFetchRun
from app.jobs.outcome import RetryAfter
from app.reconciliation import fetch_jobs, fetch_steps
from app.reconciliation.runs import (
    FETCH_SOURCE_JOB,
    SOURCE_DISABLED_ERROR,
    RunClosed,
    RunInProgress,
    adopt_run,
    finish_run_failed,
    finish_run_ok,
    open_run,
)
from app.reconciliation.types import MappedLine
from tests.gateway_support import logged
from tests.reconciliation.fetch_job_support import (
    SessionFactory,
    add_source,
    anthropic_api,
    claimed_context,
    lines,
    owner_and_project,
    payload,
    run_child,
    runs,
    serve,
    source_row,
)
from tests.reconciliation.fetch_support import DAY_FROM


@pytest.fixture
async def source_id(
    browser_factory: Any, session_factory: SessionFactory, gateway_settings: Settings
) -> uuid.UUID:
    owner = await owner_and_project(browser_factory)
    return await add_source(session_factory, gateway_settings, owner)


async def _backdate(session_factory: SessionFactory, run_id: uuid.UUID, minutes: int) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE billing_fetch_runs SET started_at = now() - make_interval(mins => :m) "
                "WHERE id = :id"
            ),
            {"id": run_id, "m": minutes},
        )
        await db.commit()


async def _open(session_factory: SessionFactory, source_id: uuid.UUID) -> uuid.UUID:
    source = await source_row(session_factory, source_id)
    async with session_factory() as db:
        run = await open_run(
            db, source, trigger="manual", day_from=DAY_FROM, day_to=DAY_FROM, requested_by=None
        )
        await db.commit()
        return run.id


async def test_a_failure_after_the_commit_leaves_the_run_ok(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    async def broken(*_: Any, **__: Any) -> list[uuid.UUID]:
        raise RuntimeError("listing projects failed")

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetch_steps, "projects_of_source", broken)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.outcome) == (JobStatus.DONE, "ok")
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("ok", None)
    assert (await source_row(session_factory, source_id)).status == "ok"
    assert logged(caplog, "billing_fetch_rollups_failed")


async def test_finishing_as_failed_never_touches_a_finished_run(
    source_id: uuid.UUID, session_factory: SessionFactory
) -> None:
    run_id = await _open(session_factory, source_id)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE billing_fetch_runs SET status = 'ok' WHERE id = :id"), {"id": run_id}
        )
        await db.execute(
            text("UPDATE billing_sources SET status = 'ok' WHERE id = :id"), {"id": source_id}
        )
        await db.commit()
        run = await db.get(BillingFetchRun, run_id)
        assert run is not None
        await finish_run_failed(db, run, error="timeout", source_status="failing")
        await db.commit()

    [stored] = await runs(session_factory, source_id)
    assert (stored["status"], stored["error"]) == ("ok", None)
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("ok", None)


async def test_a_retry_that_finds_a_run_in_progress_waits_instead_of_skipping(
    source_id: uuid.UUID, session_factory: SessionFactory, gateway_settings: Settings
) -> None:
    await _open(session_factory, source_id)  # the run a crashed first attempt left behind
    context = await claimed_context(
        session_factory, gateway_settings, FETCH_SOURCE_JOB, payload(source_id)
    )
    second = dataclasses.replace(context, job=dataclasses.replace(context.job, attempts=2))

    outcome = await fetch_jobs.run_fetch_billing_source(second, payload(source_id))

    assert outcome == RetryAfter(retry_after_s=900.0, reason="fetch in progress")
    assert len(await runs(session_factory, source_id)) == 1


async def test_the_fetch_deadline_counts_from_the_start_of_the_job(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_patterns = fetch_jobs.mapping_patterns

    async def slow_patterns(*args: Any) -> list[str]:
        await asyncio.sleep(0.4)
        return await real_patterns(*args)

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetch_jobs, "mapping_patterns", slow_patterns)
    monkeypatch.setattr(fetch_jobs, "FETCH_DEADLINE_S", 0.3)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert job.status is JobStatus.QUEUED and job.last_error == "timeout"
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "timeout")


async def test_adopting_a_manual_run_restarts_its_clock(
    source_id: uuid.UUID, session_factory: SessionFactory
) -> None:
    run_id = await _open(session_factory, source_id)
    await _backdate(session_factory, run_id, 16)  # queued for a long time
    source = await source_row(session_factory, source_id)
    async with session_factory() as db:
        adopted = await adopt_run(db, source, run_id)
        await db.commit()
    assert adopted is not None and adopted.id == run_id

    # Another fetch now sees a run in progress instead of closing this one as timed out.
    async with session_factory() as db:
        with pytest.raises(RunInProgress):
            await open_run(
                db,
                source,
                trigger="scheduled",
                day_from=DAY_FROM,
                day_to=DAY_FROM,
                requested_by=None,
            )
    [run] = await runs(session_factory, source_id)
    assert run["status"] == "running" and run["started_at"] > datetime.now(
        run["started_at"].tzinfo
    ) - timedelta(minutes=1)


async def test_a_failed_cleanup_does_not_hide_the_first_error(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    async def broken_finish(*_: Any, **__: Any) -> None:
        raise RuntimeError("finishing failed")

    async def broken_cleanup(*_: Any, **__: Any) -> None:
        raise RuntimeError("cleanup failed")

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetch_jobs, "finish_run_ok", broken_finish)
    monkeypatch.setattr(fetch_jobs, "finish_run_failed", broken_cleanup)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert job.status is JobStatus.QUEUED
    assert "finishing failed" in (job.last_error or "")
    [event] = logged(caplog, "billing_fetch_run_close_failed")
    assert event["source_id"] == str(source_id)


async def test_a_malformed_payload_ends_done_without_retries(
    session_factory: SessionFactory,
    gateway_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    job = await run_child(session_factory, gateway_settings, {"source_id": "not-a-uuid"})

    assert (job.status, job.attempts, job.outcome) == (JobStatus.DONE, 1, "ok")
    assert logged(caplog, "billing_fetch_bad_payload")


async def test_duplicate_lines_log_their_count_not_their_content(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    fetcher = fetch_jobs.fetcher_for("anthropic_admin_api")
    real_fetch = fetcher.fetch

    async def doubled(*args: Any, **kwargs: Any) -> fetch_jobs.FetchResult:
        result = await real_fetch(*args, **kwargs)
        first: MappedLine = result.lines[0]
        return fetch_jobs.FetchResult([*result.lines, first, first], result.pages)

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetcher, "fetch", doubled)
    await run_child(session_factory, gateway_settings, payload(source_id))

    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "invalid response")
    [event] = logged(caplog, "billing_fetch_duplicate_lines")
    assert event["duplicates"] == 2 and "description" not in event


async def test_a_manual_run_whose_source_was_disabled_is_closed(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await _open(session_factory, source_id)  # what the refresh route opens
    toggle = "UPDATE billing_sources SET enabled = :on WHERE id = :id"
    async with session_factory() as db:
        await db.execute(text(toggle), {"on": False, "id": source_id})
        await db.commit()
    api = anthropic_api()
    serve(monkeypatch, api)

    job = await run_child(
        session_factory, gateway_settings, payload(source_id, trigger="manual", run_id=run_id)
    )

    assert (job.status, job.outcome) == (JobStatus.DONE, "ok")
    assert api.requests == []
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", SOURCE_DISABLED_ERROR)
    assert run["finished_at"] is not None
    # Re-enabled, the source takes a new refresh at once instead of waiting 15 minutes.
    async with session_factory() as db:
        await db.execute(text(toggle), {"on": True, "id": source_id})
        await db.commit()
    assert await _open(session_factory, source_id) != run_id


async def test_finishing_as_ok_never_revives_a_closed_run(
    source_id: uuid.UUID, session_factory: SessionFactory
) -> None:
    run_id = await _open(session_factory, source_id)
    close = "UPDATE billing_fetch_runs SET status = 'failed', error = 'timeout' WHERE id = :id"
    async with session_factory() as db:
        await db.execute(text(close), {"id": run_id})
        await db.commit()
        run = await db.get(BillingFetchRun, run_id)
        assert run is not None
        with pytest.raises(RunClosed):
            await finish_run_ok(db, run, lines_upserted=3, pages=1)
        await db.rollback()

    [stored] = await runs(session_factory, source_id)
    assert (stored["status"], stored["error"], stored["lines_upserted"]) == ("failed", "timeout", 0)
    assert (await source_row(session_factory, source_id)).status == "never_fetched"


async def test_a_run_closed_while_its_fetch_worked_stays_failed(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another fetch took the run for dead and closed it: its lines are not committed under a
    run the ledger says failed, and the job ends without a retry."""
    caplog.set_level(logging.INFO)
    real_write = fetch_jobs.write_lines

    async def write_then_close(db: Any, run: BillingFetchRun, written: Any) -> int:
        count = await real_write(db, run, written)
        async with session_factory() as other:
            await other.execute(
                text(
                    "UPDATE billing_fetch_runs SET status = 'failed', error = 'timeout', "
                    "finished_at = now() WHERE id = :id"
                ),
                {"id": run.id},
            )
            await other.commit()
        return count

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetch_jobs, "write_lines", write_then_close)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.outcome) == (JobStatus.DONE, "ok")
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "timeout")
    assert await lines(session_factory, source_id) == []
    assert logged(caplog, "billing_fetch_run_closed")


async def test_a_count_the_database_cannot_hold_fails_the_run_as_an_invalid_response(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the fetchers' own checks, a value the column refuses still ends in the fixed
    vocabulary: the SQL error, which quotes the provider's line, never reaches `last_error`."""
    fetcher = fetch_jobs.fetcher_for("anthropic_admin_api")
    real_fetch = fetcher.fetch

    async def oversized(*args: Any, **kwargs: Any) -> fetch_jobs.FetchResult:
        result = await real_fetch(*args, **kwargs)
        first = dataclasses.replace(result.lines[0], quantity=2**63)
        return fetch_jobs.FetchResult([first, *result.lines[1:]], result.pages)

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetcher, "fetch", oversized)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.outcome, job.last_error) == (JobStatus.DONE, "ok", None)
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "invalid response")
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("failing", "invalid response")
    assert await lines(session_factory, source_id) == []
