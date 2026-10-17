"""The `poll_provider_status` job: every adapter in turn, one log line and one count per poll.

The job ends `done` whatever the feeds or the database did; a duplicate run is harmless.
"""

import asyncio
import logging
from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.core.observability import PROVIDER_STATUS_POLLS
from app.jobs.outcome import JobOutcome
from app.provider_status import jobs
from app.provider_status.adapters import ParsedIncident, ParseReport, ParseResult
from app.provider_status.adapters.anthropic import AnthropicAdapter, anthropic
from app.provider_status.adapters.google import google
from app.provider_status.adapters.openai import openai
from app.provider_status.jobs import run_poll_provider_status
from tests.gateway_support import logged, metric_delta
from tests.provider_status.poll_support import (
    OPENAI_RSS,
    FeedServer,
    SessionFactory,
    anthropic_feed,
    first_anthropic_incident,
    fixture,
    incidents,
    rows,
    source,
    task_context,
)

MARKER = "SECRET-BODY-MARKER-7f3a"


def _serve(monkeypatch: pytest.MonkeyPatch, server: FeedServer) -> None:
    monkeypatch.setattr(jobs, "build_status_client", server.client)


async def test_two_concurrent_runs_are_harmless(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FeedServer()
    server.serve_recorded_feeds()
    _serve(monkeypatch, server)
    context = task_context(session_factory, settings)

    results = await asyncio.gather(
        run_poll_provider_status(context, {}), run_poll_provider_status(context, {})
    )

    assert results == [None, None]
    counts = await rows(
        session_factory,
        "SELECT provider, count(*) AS n FROM provider_incidents GROUP BY provider",
    )
    assert {row["provider"]: row["n"] for row in counts} == {
        "anthropic": 10,
        "openai": 10,
        "google": 1,
    }
    for provider in ("anthropic", "openai", "google"):
        feed = await source(session_factory, provider)
        assert (feed["last_outcome"], feed["consecutive_failures"]) == ("fetched", 0)


async def test_one_feed_failing_never_stops_the_others_and_each_poll_is_counted(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    server = FeedServer()
    server.serve_recorded_feeds()
    server.answer(anthropic.url, httpx.Response(503))
    server.answer(openai.url, httpx.Response(200, content=b"not json"))
    server.answer(openai.fallback_url or "", httpx.Response(200, content=fixture(OPENAI_RSS)))
    _serve(monkeypatch, server)
    failed = metric_delta(PROVIDER_STATUS_POLLS, provider="anthropic", outcome="http_error")
    openai_parse = metric_delta(PROVIDER_STATUS_POLLS, provider="openai", outcome="parse_error")
    openai_fetched = metric_delta(PROVIDER_STATUS_POLLS, provider="openai", outcome="fetched")
    google_fetched = metric_delta(PROVIDER_STATUS_POLLS, provider="google", outcome="fetched")

    await run_poll_provider_status(task_context(session_factory, settings), {})

    assert (failed.value, google_fetched.value) == (1, 1)
    # The JSON failure is counted, and so is the poll of the RSS fallback that followed it.
    assert (openai_parse.value, openai_fetched.value) == (1, 1)
    assert len(await incidents(session_factory, "google")) == 1
    lines = {line["provider"]: line for line in logged(caplog, "provider_status_polled")}
    assert set(lines) == {"anthropic", "openai", "google"}
    assert lines["anthropic"]["outcome"] == "http_error"
    assert lines["anthropic"]["status_code"] == 503
    assert lines["openai"]["fell_back_from"] == "parse_error"
    assert lines["google"]["kept"] == 1
    for line in lines.values():
        assert {"skipped_naive", "skipped_url", "skipped_invalid", "upserted"} <= line.keys()


async def test_a_run_queued_before_polling_was_switched_off_fetches_nothing(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FeedServer()
    server.serve_recorded_feeds()
    _serve(monkeypatch, server)
    off = settings.model_copy(update={"provider_status_polling_enabled": False})

    outcome = await run_poll_provider_status(task_context(session_factory, off), {})

    assert outcome == JobOutcome.SKIPPED_NOT_CONFIGURED
    assert server.requests == []
    assert (await source(session_factory, "anthropic"))["last_polled_at"] is None


class _BrokenAnthropic(AnthropicAdapter):
    """Parses the real feed, then hands the poller an incident the database refuses."""

    def parse_with_report(self, body: bytes) -> ParseResult:
        parsed, report = super().parse_with_report(body)
        # An empty title passes no adapter but violates `provider_incidents_title_check`.
        return [replace(parsed[0], title="")], ParseReport(1, 0, 0, report.skipped_invalid)


async def test_a_database_error_during_the_upsert_is_recorded_and_the_job_ends_done(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FeedServer()
    server.serve_recorded_feeds()
    _serve(monkeypatch, server)
    monkeypatch.setattr(jobs, "ADAPTERS", (_BrokenAnthropic(), google))
    counted = metric_delta(PROVIDER_STATUS_POLLS, provider="anthropic", outcome="parse_error")

    assert await run_poll_provider_status(task_context(session_factory, settings), {}) is None

    assert counted.value == 1
    assert await incidents(session_factory, "anthropic") == []
    feed = await source(session_factory, "anthropic")
    assert feed["last_outcome"] == "parse_error"
    assert feed["last_error_type"] == "IntegrityError"
    assert feed["consecutive_failures"] == 1
    # The next adapter still ran on a clean session.
    assert len(await incidents(session_factory, "google")) == 1


async def test_log_lines_never_carry_the_body(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    server = FeedServer()
    server.serve_recorded_feeds()
    marked = first_anthropic_incident(name=f"Outage {MARKER}")
    server.answer(anthropic.url, httpx.Response(200, content=anthropic_feed(marked)))
    server.answer(openai.url, httpx.Response(500, content=MARKER.encode()))
    server.answer(openai.fallback_url or "", httpx.Response(200, content=f"<{MARKER}".encode()))
    server.answer(google.url, httpx.Response(200, content=MARKER.encode()))
    _serve(monkeypatch, server)

    await run_poll_provider_status(task_context(session_factory, settings), {})

    assert MARKER not in caplog.text
    assert len(logged(caplog, "provider_status_polled")) == 3
    [row] = await incidents(session_factory, "anthropic")
    assert MARKER in row["title"]


def test_parsed_incident_is_the_only_row_shape() -> None:
    # The upsert builds its rows from `ParsedIncident` fields only; this pins the field list
    # the statement relies on, so a new adapter field cannot be written without a review.
    assert list(ParsedIncident.__dataclass_fields__) == [
        "external_id",
        "title",
        "impact",
        "status",
        "api_affected",
        "components",
        "started_at",
        "resolved_at",
        "url",
        "latest_update",
        "feed_updated_at",
    ]
