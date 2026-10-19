"""A gateway span whose write fails is retried; one still lost is counted, never silent.

The client's answer never waits on its span. A write that fails is retried after each of
`RECORD_RETRY_DELAYS_S`, in one transaction each time, so a retry after a failure stores the
span and counts the key's minute once, even when the failed try had committed after all. A
span for a deleted project is not retried. A span still not stored after the last retry, or dropped
because the write backlog was full, is counted in `spanlight_gateway_record_failures_total` and
in `gateway_spans_lost` on `/health/ready`, which stays informational (never a 503).
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.observability import GATEWAY_RECORD_FAILURES
from app.gateway import key_limits, recorder, span_write
from app.gateway.context import GatewayResult
from app.gateway.execute import execute
from app.ingest import pipeline
from app.ingest.pipeline import ProjectGoneError
from app.jobs.worker_heartbeat import beat
from tests.gateway.support import CHAT_BODY, chat_request, key_minute_counts
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    drain_recorder,
    fixture_text,
    logged,
    metric_delta,
    read_gateway_span,
    read_gateway_spans,
)

CALL_PATH = "/gw/v1/chat/completions"
# `chat.json` usage: 19 input and 9 output tokens.
CHAT_TOKENS = 28


@pytest.fixture
def gateway_settings(gateway_settings: Settings) -> Settings:
    """One span written at a time and one waiting at most, so a second span overflows."""
    return gateway_settings.model_copy(
        update={"gateway_record_backlog": 1, "gateway_record_concurrency": 1}
    )


@pytest.fixture(autouse=True)
async def own_recorder(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Writers built from this module's settings, drained before the earlier ones come back.

    The recorder keeps one set of writers per event loop, and the tests share one loop.
    """
    monkeypatch.setattr(recorder, "_writers", None)
    yield
    await drain_recorder()


@dataclass
class FailingWrites:
    """Every span write fails with `error`; `writes` counts the tries, `waits` the backoff."""

    error: Exception = field(default_factory=lambda: ConnectionResetError("database went away"))
    writes: int = 0
    waits: list[float] = field(default_factory=list)

    async def ingest(self, *_: Any, **__: Any) -> Any:
        self.writes += 1
        raise self.error

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)  # the backoff, without waiting it


@pytest.fixture
def failing_writes(monkeypatch: pytest.MonkeyPatch) -> FailingWrites:
    failing = FailingWrites()
    monkeypatch.setattr(span_write, "ingest_spans", failing.ingest)
    return failing


async def _call(
    gateway_context: GatewayContextFactory, failing: FailingWrites, *, keyed: bool
) -> GatewayResult:
    """One non-streaming call whose span write then fails, the recorder drained."""
    context = await gateway_context(keyed=keyed)
    result = await execute(
        chat_request(stream=False), dataclasses.replace(context, sleep=failing.sleep)
    )
    await drain_recorder()
    return result


def _answer() -> bytes:
    return fixture_text("openai/chat.json").encode()


async def _spans_lost(client: httpx.AsyncClient) -> Any:
    """`gateway_spans_lost` from `/health/ready` (the other fields are not this file's concern)."""
    return (await client.get("/health/ready")).json()["gateway_spans_lost"]


async def test_a_span_write_that_fails_once_is_retried_and_stored_once(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_json(_answer())
    real_record_minute = key_limits.record_minute
    calls = 0

    async def flaky_record_minute(*args: Any, **kwargs: Any) -> None:
        # Fails after the span was written in the same transaction, which then rolls back.
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError("the database went away")
        await real_record_minute(*args, **kwargs)

    monkeypatch.setattr(span_write, "record_minute", flaky_record_minute)
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = await _spans_lost(gateway_client)

    response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)

    assert response.status_code == 200
    assert response.content == _answer()
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "ok"
    assert calls == 2
    # The key's minute counts the call once: the failed attempt left nothing behind.
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (
        1,
        CHAT_TOKENS,
    )
    assert errors.value == 0
    assert await _spans_lost(gateway_client) == lost


async def test_recorder_failure_is_counted_and_the_client_still_gets_its_answer(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    failing_writes: FailingWrites,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_provider.reply_json(_answer())
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = await _spans_lost(gateway_client)

    result = await _call(gateway_context, failing_writes, keyed=True)

    delays = list(recorder.RECORD_RETRY_DELAYS_S)
    assert (result.status, result.body) == (200, _answer())
    assert (failing_writes.waits, failing_writes.writes) == (delays, len(delays) + 1)
    retries = logged(caplog, "gateway.record_retry")
    assert [entry["retry_in_s"] for entry in retries] == delays
    assert len(logged(caplog, "gateway.record_failed")) == 1
    assert errors.value == 1
    assert await read_gateway_spans(session_factory, gateway_workspace.project_id) == []
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (0, 0)
    # The loss is on the readiness probe, which it never fails.
    await beat(session_factory, "test-worker", "test")
    ready = await gateway_client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["gateway_spans_lost"] == lost + 1


async def test_a_retry_after_a_commit_whose_answer_was_lost_counts_the_minute_once(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_json(_answer())
    real_write = span_write.write_span
    tries: list[bool] = []

    async def ack_lost_once(*args: Any, **kwargs: Any) -> Any:
        tries.append(kwargs["retry"])
        outcome = await real_write(*args, **kwargs)
        if len(tries) == 1:
            raise ConnectionResetError("the commit went through, its answer did not")
        return outcome

    monkeypatch.setattr(recorder, "write_span", ack_lost_once)
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")

    response = await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)

    assert response.status_code == 200
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "ok"
    assert tries == [False, True]
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (
        1,
        CHAT_TOKENS,
    )
    assert errors.value == 0


async def test_recorder_backlog_overflow_drops_and_counts(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for _ in range(3):
        fake_provider.reply_json(_answer())
    release = asyncio.Event()
    real_ingest = pipeline.ingest_spans

    async def held_ingest(*args: Any, **kwargs: Any) -> Any:
        await release.wait()  # the first span's write holds the only slot until released
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", held_ingest)
    overflow = metric_delta(GATEWAY_RECORD_FAILURES, reason="overflow")
    lost = await _spans_lost(gateway_client)

    responses = [await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY) for _ in range(3)]

    assert [(r.status_code, r.content) for r in responses] == [(200, _answer())] * 3
    assert overflow.value == 2
    assert len(logged(caplog, "gateway.record_overflow")) == 1  # at most one line a minute
    assert await _spans_lost(gateway_client) == lost + 2
    release.set()
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "ok"


async def test_a_span_for_a_deleted_project_is_not_retried(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    failing_writes: FailingWrites,
) -> None:
    fake_provider.reply_json(_answer())
    failing_writes.error = ProjectGoneError("the project was deleted")
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = recorder.spans_lost()

    result = await _call(gateway_context, failing_writes, keyed=False)

    assert result.status == 200
    # Every retry would find the project deleted too.
    assert (failing_writes.writes, failing_writes.waits) == (1, [])
    assert errors.value == 1
    assert recorder.spans_lost() == lost + 1


async def test_recorder_backlog_bytes_drop_a_large_span_and_keep_small_ones(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for _ in range(3):
        fake_provider.reply_json(_answer())
    release = asyncio.Event()
    real_ingest = pipeline.ingest_spans

    async def held_ingest(*args: Any, **kwargs: Any) -> Any:
        await release.wait()
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", held_ingest)
    overflow = metric_delta(GATEWAY_RECORD_FAILURES, reason="overflow")
    large = {**CHAT_BODY, "messages": [{"role": "user", "content": "x" * 200_000}]}

    await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)  # builds the writers, held
    writers = recorder._writers
    assert writers is not None
    writers.backlog, writers.backlog_bytes = 10, 150_000  # room by count, not for 200 KB
    await gateway_workspace.gw.post(CALL_PATH, json=large)
    await gateway_workspace.gw.post(CALL_PATH, json=CHAT_BODY)

    assert overflow.value == 1
    assert len(writers.pending) == 2
    release.set()
    await drain_recorder()
    assert writers.held_bytes == 0
