"""The billing fetch jobs and the run ledger (spec §10.1, §10.2; real Postgres).

Failure modes: a failed fetch leaves half a window of lines behind, so the report compares the
provider's bill with part of it; a correction the provider made becomes a second row instead of
replacing the first; a EUR amount is stored as dollars; a wrong key is retried forever, or a
rate-limited provider is hammered; a disabled or CSV source is fetched; the daily parent
enqueues a source twice; the rollups the report compares against stay stale after a fetch.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from prometheus_client import REGISTRY
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.config import Settings
from app.core.observability import BILLING_FETCH_RUNS, BILLING_LINES_UPSERTED
from app.db.models import Job, JobStatus
from app.db.rls import bypass_rls
from app.jobs.outcome import JobOutcome
from app.jobs.scheduler import FETCH_BILLING_SOURCES, periodic_jobs
from app.jobs.tasks import TASKS
from app.reconciliation import fetch_jobs
from app.reconciliation.fetch_jobs import (
    fetch_window,
    retry_for,
    run_fetch_billing_source,
    run_fetch_billing_sources,
)
from app.reconciliation.fetchers import FetchFailed
from app.reconciliation.runs import FETCH_SOURCE_JOB, RunInProgress, open_run
from tests.gateway_support import metric_delta
from tests.reconciliation.fetch_job_support import (
    NOW,
    TODAY,
    Owner,
    SessionFactory,
    add_source,
    anthropic_api,
    claimed_context,
    cost_page_with,
    failing_api,
    lines,
    owner_and_project,
    payload,
    rerun_now,
    run_child,
    runs,
    serve,
    source_row,
)
from tests.reconciliation.fetch_support import ANTHROPIC_COST_PATH, DAY_FROM, Reply, ScriptedApi

INPUT_LINE = "Claude Sonnet 4.5 Usage - Input Tokens"


@pytest.fixture
async def owner(browser_factory: Any) -> Owner:
    return await owner_and_project(browser_factory)


@pytest.fixture
async def source_id(
    owner: Owner, session_factory: SessionFactory, gateway_settings: Settings
) -> uuid.UUID:
    return await add_source(session_factory, gateway_settings, owner)


def _seconds_until(when: datetime) -> float:
    return (when - datetime.now(UTC)).total_seconds()


# --- pure policy ----------------------------------------------------------------------------


def test_fetch_window_is_seven_days_or_thirty_one_on_a_first_fetch() -> None:
    assert fetch_window(NOW, first_fetch=False) == (
        TODAY - timedelta(days=7),
        TODAY - timedelta(days=1),
    )
    assert fetch_window(NOW, first_fetch=True) == (
        TODAY - timedelta(days=31),
        TODAY - timedelta(days=1),
    )


def test_retry_for_auth_and_invalid_is_none() -> None:
    assert retry_for(FetchFailed("401 Unauthorized", retry="none"), 1) is None
    assert retry_for(FetchFailed("invalid response", retry="none"), 1) is None


def test_retry_for_rate_limited_honours_retry_after_up_to_15_minutes() -> None:
    assert retry_for(FetchFailed("429", retry="rate_limited", retry_after_s=120), 1) == timedelta(
        minutes=2
    )
    assert retry_for(FetchFailed("429", retry="rate_limited", retry_after_s=86400), 1) == timedelta(
        minutes=15
    )
    assert retry_for(FetchFailed("429", retry="rate_limited"), 2) == timedelta(minutes=15)


def test_retry_for_transient_waits_5_then_30_minutes_then_gives_up() -> None:
    failure = FetchFailed("503 Service Unavailable", retry="transient")
    assert [retry_for(failure, attempt) for attempt in (1, 2, 3)] == [
        timedelta(minutes=5),
        timedelta(minutes=30),
        None,
    ]


# --- the child job --------------------------------------------------------------------------


async def test_first_fetch_uses_31_days_then_7(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def child_windows() -> list[tuple[str, str]]:
        async with session_factory() as db:
            jobs = (
                await db.scalars(select(Job).where(Job.kind == FETCH_SOURCE_JOB).order_by(Job.id))
            ).all()
        return [(job.payload["day_from"], job.payload["day_to"]) for job in jobs]

    parent = await claimed_context(session_factory, gateway_settings, "fetch_billing_sources")
    assert await run_fetch_billing_sources(parent, {}) is JobOutcome.OK
    assert await child_windows() == [("2026-09-15", "2026-10-15")]

    api = anthropic_api()
    serve(monkeypatch, api)
    async with session_factory() as db:
        [job] = (await db.scalars(select(Job).where(Job.kind == FETCH_SOURCE_JOB))).all()
    child = await claimed_context(session_factory, gateway_settings, FETCH_SOURCE_JOB, job.payload)
    assert await run_fetch_billing_source(child, job.payload) is JobOutcome.OK
    assert ("starting_at", "2026-09-15T00:00:00Z") in api.requests[0].url.params.multi_items()

    tomorrow = NOW + timedelta(days=1)
    parent = await claimed_context(
        session_factory, gateway_settings, "fetch_billing_sources", now=tomorrow
    )
    await run_fetch_billing_sources(parent, {})
    assert (await child_windows())[-1] == ("2026-10-10", "2026-10-16")


async def test_lines_and_run_and_source_written_in_one_transaction(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, anthropic_api())
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.outcome) == (JobStatus.DONE, "ok")
    [run] = await runs(session_factory, source_id)
    stored = await lines(session_factory, source_id)
    assert (run["status"], run["error"], run["pages"]) == ("ok", None, 2)
    assert run["trigger"] == "scheduled" and run["finished_at"] is not None
    assert run["lines_upserted"] == len(stored) > 0
    assert {line["fetch_run_id"] for line in stored} == {run["id"]}
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("ok", None)
    assert source.last_success_at is not None and source.last_fetched_at is not None

    # A failure after the lines are written rolls them back with the run's and source's update.
    second = await add_source(
        session_factory, gateway_settings, await _other_owner(session_factory, source_id), name="B"
    )

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("finishing failed")

    serve(monkeypatch, anthropic_api())
    monkeypatch.setattr(fetch_jobs, "finish_run_ok", broken)
    failed = await run_child(session_factory, gateway_settings, payload(second))
    assert failed.status is JobStatus.QUEUED and "finishing failed" in (failed.last_error or "")
    assert await lines(session_factory, second) == []
    [abandoned] = await runs(session_factory, second)
    assert (abandoned["status"], abandoned["error"]) == ("failed", None)
    assert (await source_row(session_factory, second)).status == "never_fetched"


async def _other_owner(session_factory: SessionFactory, source_id: uuid.UUID) -> Owner:
    """The same organization, with a second project for a second source to map."""
    async with session_factory() as db:
        org_id, user_id = (
            await db.execute(
                text("SELECT org_id, created_by FROM billing_sources WHERE id = :id"),
                {"id": source_id},
            )
        ).one()
        project_id = uuid.uuid4()
        await db.execute(
            text("INSERT INTO projects (id, org_id, name, slug) VALUES (:id, :org, 'Two', 'two')"),
            {"id": project_id, "org": org_id},
        )
        await db.commit()
    return Owner(user_id=user_id, org_id=org_id, project_id=project_id)


async def test_refetch_updates_corrected_amount_on_same_key(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, anthropic_api())
    await run_child(session_factory, gateway_settings, payload(source_id))
    before = await lines(session_factory, source_id)
    [first] = [
        line for line in before if line["description"] == INPUT_LINE and line["day"] == DAY_FROM
    ]
    assert first["amount_usd"] == Decimal("1.2345")

    serve(monkeypatch, anthropic_api(cost_page_with(amount="130.00")))
    await run_child(session_factory, gateway_settings, payload(source_id))

    after = await lines(session_factory, source_id)
    assert len(after) == len(before)
    [corrected] = [
        line for line in after if line["line_key"] == first["line_key"] and line["day"] == DAY_FROM
    ]
    first_run, second_run = await runs(session_factory, source_id)
    assert corrected["id"] == first["id"]
    assert corrected["amount_usd"] == Decimal("1.30000000")
    assert corrected["fetch_run_id"] == second_run["id"] != first_run["id"]
    assert corrected["first_seen_at"] == first["first_seen_at"]
    assert corrected["last_seen_at"] > first["last_seen_at"]


async def test_unsupported_currency_writes_nothing(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, anthropic_api())
    await run_child(session_factory, gateway_settings, payload(source_id))
    lines_before = await lines(session_factory, source_id)
    runs_before = await runs(session_factory, source_id)

    serve(monkeypatch, anthropic_api(cost_page_with(currency="EUR", amount="999.00")))
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert job.status is JobStatus.DONE
    assert await lines(session_factory, source_id) == lines_before
    runs_after = await runs(session_factory, source_id)
    assert len(runs_after) == len(runs_before) + 1
    assert (runs_after[-1]["status"], runs_after[-1]["error"]) == ("failed", "unsupported currency")
    assert runs_after[-1]["lines_upserted"] == 0
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("failing", "unsupported currency")


async def test_401_marks_failing_without_retry(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = failing_api(401, body=b'{"error": "invalid x-api-key"}')
    serve(monkeypatch, api)
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.attempts) == (JobStatus.DONE, 1)
    assert len(api.requests) == 1
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "401 Unauthorized")
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("failing", "401 Unauthorized")
    assert source.last_success_at is None


async def test_429_retries_with_retry_after_capped(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, failing_api(429, headers={"retry-after": "3600"}))
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.attempts, job.last_error) == (
        JobStatus.QUEUED,
        1,
        "429 Too Many Requests",
    )
    assert 14 * 60 < _seconds_until(job.run_after) <= 15 * 60
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "429 Too Many Requests")
    # Not the last attempt: the source keeps its status until the retries are spent.
    assert (await source_row(session_factory, source_id)).status == "never_fetched"

    serve(monkeypatch, failing_api(429, headers={"retry-after": "90"}))
    job = await rerun_now(session_factory, gateway_settings)
    assert job.attempts == 2 and 80 < _seconds_until(job.run_after) <= 90


async def test_5xx_retries_then_failing_after_third(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, failing_api(503))
    job = await run_child(session_factory, gateway_settings, payload(source_id))
    assert job.status is JobStatus.QUEUED and 4 * 60 < _seconds_until(job.run_after) <= 5 * 60
    assert (await source_row(session_factory, source_id)).status == "never_fetched"

    job = await rerun_now(session_factory, gateway_settings)
    assert job.status is JobStatus.QUEUED and 29 * 60 < _seconds_until(job.run_after) <= 30 * 60

    job = await rerun_now(session_factory, gateway_settings)
    assert (job.status, job.attempts) == (JobStatus.DONE, 3)
    assert [run["status"] for run in await runs(session_factory, source_id)] == ["failed"] * 3
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("failing", "503 Service Unavailable")
    assert await lines(session_factory, source_id) == []


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (Reply(body=b"x" * 2048), "response too large"),
        (Reply(body=b'{"data": [], "has_more": fal'), "invalid response"),
        (
            Reply(json={"data": [{"starting_at": 7, "results": []}], "has_more": False}),
            "invalid response",
        ),
    ],
    ids=["oversize", "truncated", "malformed"],
)
async def test_oversize_and_malformed_fail_cleanly(
    reply: Reply,
    error: str,
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.reconciliation.fetchers.base.MAX_RESPONSE_BYTES", 1024)
    serve(monkeypatch, ScriptedApi(routes={ANTHROPIC_COST_PATH: [reply]}))
    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert (job.status, job.attempts) == (JobStatus.DONE, 1)
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", error)
    assert (await source_row(session_factory, source_id)).last_error == error
    assert await lines(session_factory, source_id) == []


def _duration_count() -> float:
    sample = "spanlight_billing_fetch_duration_seconds_count"
    return REGISTRY.get_sample_value(sample, {"provider": "anthropic"}) or 0.0


async def test_metrics_outcomes(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outcome(name: str) -> Any:
        return metric_delta(BILLING_FETCH_RUNS, provider="anthropic", outcome=name)

    ok, upserted, durations = (
        outcome("ok"),
        metric_delta(BILLING_LINES_UPSERTED, provider="anthropic"),
        _duration_count(),
    )
    serve(monkeypatch, anthropic_api())
    await run_child(session_factory, gateway_settings, payload(source_id))
    assert ok.value == 1
    assert upserted.value == len(await lines(session_factory, source_id)) > 0
    assert _duration_count() == durations + 1

    for status, name in ((401, "auth_failed"), (429, "rate_limited"), (503, "provider_error")):
        counted = outcome(name)
        serve(monkeypatch, failing_api(status))
        await run_child(session_factory, gateway_settings, payload(source_id))
        assert counted.value == 1, name

    invalid = outcome("invalid")
    serve(monkeypatch, ScriptedApi(routes={ANTHROPIC_COST_PATH: [Reply(body=b"not json")]}))
    await run_child(session_factory, gateway_settings, payload(source_id))
    assert invalid.value == 1

    skipped = outcome("skipped")
    async with session_factory() as db:
        await db.execute(
            text("UPDATE billing_sources SET enabled = false WHERE id = :id"), {"id": source_id}
        )
        await db.commit()
    await run_child(session_factory, gateway_settings, payload(source_id))
    assert skipped.value == 1


_INSERT_SPAN = """
    WITH new_trace AS (
        INSERT INTO traces (project_id, trace_id, started_at, ended_at)
        VALUES (:project_id, :trace_id, :started_at, :started_at + interval '1 second')
    )
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, input_tokens, output_tokens, cost_usd, pricing_version)
    VALUES (:project_id, :trace_id, :span_id, 'llm', 'call', 'ok', :started_at,
            :started_at + interval '1 second', 'anthropic', 'claude-sonnet-4-5', 100, 10,
            0.001, '2026-10-16')
"""


async def _llm_span(session_factory: SessionFactory, project_id: uuid.UUID) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(_INSERT_SPAN),
            {
                "project_id": project_id,
                "trace_id": uuid.uuid4().hex,
                "span_id": uuid.uuid4().hex[:16],
                "started_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
            },
        )
        await db.commit()


async def test_rollups_refreshed_for_mapped_projects(
    source_id: uuid.UUID,
    owner: Owner,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = await _other_owner(session_factory, source_id)  # a project the source does not map
    await _llm_span(session_factory, owner.project_id)
    await _llm_span(session_factory, other.project_id)

    serve(monkeypatch, anthropic_api())
    await run_child(session_factory, gateway_settings, payload(source_id))

    async with session_factory() as db:
        await bypass_rls(db)
        built = (
            await db.execute(text("SELECT project_id, day, calls FROM cost_rollups_daily"))
        ).all()
    assert [tuple(row) for row in built] == [(owner.project_id, DAY_FROM, 1)]


async def test_child_adopts_the_run_a_manual_refresh_opened(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as db:
        source = await source_row(session_factory, source_id)
        opened = await open_run(
            db, source, trigger="manual", day_from=DAY_FROM, day_to=DAY_FROM, requested_by=None
        )
        await db.commit()
        opened_id = opened.id
        with pytest.raises(RunInProgress):
            await open_run(
                db, source, trigger="manual", day_from=DAY_FROM, day_to=DAY_FROM, requested_by=None
            )
        await db.rollback()

    serve(monkeypatch, anthropic_api())
    await run_child(
        session_factory, gateway_settings, payload(source_id, trigger="manual", run_id=opened_id)
    )

    [run] = await runs(session_factory, source_id)
    assert (run["id"], run["status"], run["trigger"]) == (opened_id, "ok", "manual")


async def test_a_run_left_running_for_15_minutes_is_closed_as_timed_out(
    source_id: uuid.UUID, session_factory: SessionFactory
) -> None:
    source = await source_row(session_factory, source_id)
    async with session_factory() as db:
        stale = await open_run(
            db, source, trigger="scheduled", day_from=DAY_FROM, day_to=DAY_FROM, requested_by=None
        )
        await db.execute(
            text(
                "UPDATE billing_fetch_runs SET started_at = now() - interval '16 minutes' "
                "WHERE id = :id"
            ),
            {"id": stale.id},
        )
        await db.commit()
        fresh = await open_run(
            db, source, trigger="scheduled", day_from=DAY_FROM, day_to=DAY_FROM, requested_by=None
        )
        await db.commit()

    by_id = {run["id"]: run for run in await runs(session_factory, source_id)}
    assert (by_id[stale.id]["status"], by_id[stale.id]["error"]) == ("failed", "timeout")
    assert by_id[fresh.id]["status"] == "running"


async def test_two_runs_opened_at_once_make_one(
    source_id: uuid.UUID, session_factory: SessionFactory
) -> None:
    source = await source_row(session_factory, source_id)

    async def open_one() -> str:
        async with session_factory() as db:
            try:
                await open_run(
                    db,
                    source,
                    trigger="manual",
                    day_from=DAY_FROM,
                    day_to=DAY_FROM,
                    requested_by=None,
                )
                await asyncio.sleep(0.05)  # hold the source lock while the other one waits
                await db.commit()
            except RunInProgress:
                return "in progress"
        return "opened"

    assert sorted(await asyncio.gather(open_one(), open_one())) == ["in progress", "opened"]
    assert len(await runs(session_factory, source_id)) == 1


async def test_crypto_not_configured_marks_failing(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    settings: Settings,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = anthropic_api()
    serve(monkeypatch, api)
    job = await run_child(session_factory, settings, payload(source_id))  # no CREDENTIALS_KEYS

    assert (job.status, job.outcome) == (JobStatus.DONE, "skipped_not_configured")
    assert api.requests == []
    [run] = await runs(session_factory, source_id)
    assert (run["status"], run["error"]) == ("failed", "CREDENTIALS_KEYS is not set")
    source = await source_row(session_factory, source_id)
    assert (source.status, source.last_error) == ("failing", "CREDENTIALS_KEYS is not set")

    other_keyring = gateway_settings.model_copy(
        update={"credentials_keys": SecretStr("elsewhere:" + "A" * 43 + "=")}
    )
    job = await run_child(session_factory, other_keyring, payload(source_id))
    assert job.status is JobStatus.DONE and api.requests == []
    assert (await source_row(session_factory, source_id)).last_error == "key cannot be decrypted"


# --- the parent job and scheduling ----------------------------------------------------------


async def test_parent_enqueues_one_child_per_enabled_source_with_dedupe(
    owner: Owner,
    browser_factory: Any,
    session_factory: SessionFactory,
    gateway_settings: Settings,
) -> None:
    anthropic = await add_source(session_factory, gateway_settings, owner)
    openai = await add_source(
        session_factory, gateway_settings, owner, kind="openai_costs_api", name="OpenAI"
    )
    second = await owner_and_project(browser_factory, "second@example.com")
    disabled = await add_source(session_factory, gateway_settings, second)
    await add_source(
        session_factory, gateway_settings, second, kind="csv_upload", name="CSV", provider="openai"
    )
    async with session_factory() as db:
        await db.execute(
            text("UPDATE billing_sources SET enabled = false WHERE id = :id"), {"id": disabled}
        )
        await db.execute(
            text("UPDATE billing_sources SET last_success_at = now() WHERE id = :id"),
            {"id": openai},
        )
        await db.commit()

    parent = await claimed_context(session_factory, gateway_settings, "fetch_billing_sources")
    assert await run_fetch_billing_sources(parent, {}) is JobOutcome.OK
    again = await claimed_context(session_factory, gateway_settings, "fetch_billing_sources")
    await run_fetch_billing_sources(again, {})

    async with session_factory() as db:
        children = (await db.scalars(select(Job).where(Job.kind == FETCH_SOURCE_JOB))).all()
    by_source = {job.payload["source_id"]: job for job in children}
    assert set(by_source) == {str(anthropic), str(openai)}
    for source, job in by_source.items():
        assert job.dedupe_key == f"fetch_billing_source:{source}:2026-10-16"
        assert job.max_attempts == 3
        assert job.payload["trigger"] == "scheduled"
    assert by_source[str(anthropic)].payload["day_from"] == "2026-09-15"
    assert by_source[str(openai)].payload["day_from"] == "2026-10-09"


async def test_parent_closes_runs_left_running_for_15_minutes(
    owner: Owner, session_factory: SessionFactory, gateway_settings: Settings
) -> None:
    """Only opening a new run closed a dead one, so a source disabled after its worker crashed
    showed `running` in its runs list for good. The daily parent closes every such run."""
    stale = await add_source(session_factory, gateway_settings, owner)
    disabled = await add_source(
        session_factory, gateway_settings, owner, kind="openai_costs_api", name="OpenAI"
    )
    fresh = await add_source(
        session_factory, gateway_settings, owner, name="Second account", projects=[]
    )
    opened = {}
    for source_id, minutes in ((stale, 16), (disabled, 60), (fresh, 5)):
        source = await source_row(session_factory, source_id)
        async with session_factory() as db:
            run = await open_run(
                db,
                source,
                trigger="scheduled",
                day_from=DAY_FROM,
                day_to=DAY_FROM,
                requested_by=None,
            )
            await db.execute(
                text(
                    "UPDATE billing_fetch_runs SET started_at = now() - make_interval(mins => :m) "
                    "WHERE id = :id"
                ),
                {"id": run.id, "m": minutes},
            )
            await db.commit()
        opened[source_id] = run.id
    async with session_factory() as db:
        await db.execute(
            text("UPDATE billing_sources SET enabled = false WHERE id = :id"), {"id": disabled}
        )
        await db.commit()

    parent = await claimed_context(session_factory, gateway_settings, "fetch_billing_sources")
    assert await run_fetch_billing_sources(parent, {}) is JobOutcome.OK

    by_id = {row["id"]: row for row in await all_runs(session_factory)}
    for source_id in (stale, disabled):
        closed = by_id[opened[source_id]]
        assert (closed["status"], closed["error"]) == ("failed", "timeout")
        assert closed["finished_at"] is not None
    assert by_id[opened[fresh]]["status"] == "running"


async def all_runs(session_factory: SessionFactory) -> list[dict[str, Any]]:
    async with session_factory() as db:
        result = await db.execute(text("SELECT * FROM billing_fetch_runs"))
        return [dict(row) for row in result.mappings()]


async def test_parent_does_nothing_when_billing_sources_are_off(
    source_id: uuid.UUID, session_factory: SessionFactory, gateway_settings: Settings
) -> None:
    off = gateway_settings.model_copy(update={"billing_sources_enabled": False})
    parent = await claimed_context(session_factory, off, "fetch_billing_sources")
    assert await run_fetch_billing_sources(parent, {}) is JobOutcome.SKIPPED_NOT_CONFIGURED
    async with session_factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(Job).where(Job.kind == FETCH_SOURCE_JOB)
        )
    assert count == 0


def test_parent_is_scheduled_daily_at_0330_only_when_enabled(settings: Settings) -> None:
    assert (FETCH_BILLING_SOURCES.kind, FETCH_BILLING_SOURCES.every) == (
        "fetch_billing_sources",
        timedelta(days=1),
    )
    assert FETCH_BILLING_SOURCES.max_attempts == 1
    assert FETCH_BILLING_SOURCES.run_after_offset == timedelta(hours=3, minutes=30)
    assert FETCH_BILLING_SOURCES in periodic_jobs(settings)
    off = settings.model_copy(update={"billing_sources_enabled": False})
    assert FETCH_BILLING_SOURCES not in periodic_jobs(off)
    assert TASKS["fetch_billing_sources"] is run_fetch_billing_sources
    assert TASKS[FETCH_SOURCE_JOB] is run_fetch_billing_source
