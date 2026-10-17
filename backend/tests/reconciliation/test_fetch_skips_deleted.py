"""A fetch job whose source went away, and what a fetch job writes to the logs (spec §10.2, §12).

Failure modes: a job queued before its source was deleted or disabled still calls the provider
with the old key, or crashes and retries; a source deleted while its fetch is in flight leaves
lines behind or fails the job three times; a log line carries the admin key or a provider's
response body (which can echo the key).
"""

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.core.observability import BILLING_FETCH_RUNS
from app.db.models import JobStatus
from app.reconciliation.fetchers import FETCHERS, FetchResult
from tests.gateway_support import logged, metric_delta
from tests.reconciliation.fetch_job_support import (
    SessionFactory,
    add_source,
    anthropic_api,
    failing_api,
    lines,
    owner_and_project,
    payload,
    rows,
    run_child,
    runs,
    serve,
)
from tests.reconciliation.fetch_support import ANTHROPIC_KEY
from tests.reconciliation.sources_support import secret_windows

BODY_MARKER = "provider-body-marker-7f3a"


@pytest.fixture
async def source_id(
    browser_factory: Any, session_factory: SessionFactory, gateway_settings: Settings
) -> uuid.UUID:
    owner = await owner_and_project(browser_factory)
    return await add_source(session_factory, gateway_settings, owner)


async def _execute(session_factory: SessionFactory, sql: str, source_id: uuid.UUID) -> None:
    async with session_factory() as db:
        await db.execute(text(sql), {"id": source_id})
        await db.commit()


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM billing_sources WHERE id = :id",
        "UPDATE billing_sources SET enabled = false WHERE id = :id",
    ],
    ids=["deleted", "disabled"],
)
async def test_a_job_for_a_deleted_or_disabled_source_ends_skipped(
    change: str,
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    api = anthropic_api()
    serve(monkeypatch, api)
    await _execute(session_factory, change, source_id)
    skipped = metric_delta(
        BILLING_FETCH_RUNS,
        provider="unknown" if "DELETE" in change else "anthropic",
        outcome="skipped",
    )

    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.attempts) == (JobStatus.DONE, 1)
    assert api.requests == []
    assert await runs(session_factory, source_id) == []
    assert await lines(session_factory, source_id) == []
    assert skipped.value == 1
    [event] = logged(caplog, "billing_fetch_skipped")
    assert event["source_id"] == str(source_id)


async def test_source_deleted_mid_run_ends_skipped(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    serve(monkeypatch, anthropic_api())
    fetcher = FETCHERS["anthropic_admin_api"]
    real_fetch = fetcher.fetch

    async def fetch_then_delete(*args: Any, **kwargs: Any) -> FetchResult:
        result = await real_fetch(*args, **kwargs)
        await _execute(session_factory, "DELETE FROM billing_sources WHERE id = :id", source_id)
        return result

    monkeypatch.setattr(fetcher, "fetch", fetch_then_delete)
    skipped = metric_delta(BILLING_FETCH_RUNS, provider="anthropic", outcome="skipped")

    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.attempts, job.outcome) == (JobStatus.DONE, 1, "ok")
    assert await rows(session_factory, "SELECT id FROM provider_cost_lines") == []
    assert await rows(session_factory, "SELECT id FROM billing_fetch_runs") == []
    assert skipped.value == 1
    [gone] = logged(caplog, "billing_fetch_source_gone")
    assert gone["source_id"] == str(source_id)


async def test_logs_hold_no_key_or_body(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    echo = f'{{"error": "{BODY_MARKER}", "key": "{ANTHROPIC_KEY}"}}'.encode()
    for api in (anthropic_api(), failing_api(401, body=echo), failing_api(503, body=echo)):
        serve(monkeypatch, api)
        await run_child(session_factory, gateway_settings, payload(source_id))
    assert {run["status"] for run in await runs(session_factory, source_id)} == {"ok", "failed"}

    text_logged = "\n".join(f"{record.getMessage()} {record.msg!r}" for record in caplog.records)
    assert logged(caplog, "billing_fetch_failed"), "the failures were logged"
    assert str(source_id) in text_logged
    assert BODY_MARKER not in text_logged
    leaked = [window for window in secret_windows(ANTHROPIC_KEY) if window in text_logged]
    assert leaked == []
    for event in logged(caplog, "billing_fetch_failed") + logged(caplog, "billing_fetch_done"):
        assert set(event) <= {
            "event",
            "level",
            "logger",
            "timestamp",
            "request_id",
            "source_id",
            "run_id",
            "kind",
            "provider",
            "endpoint",
            "page",
            "status_line",
            "retry",
            "attempt",
            "final",
            "lines",
            "pages",
            "duration_s",
        }
