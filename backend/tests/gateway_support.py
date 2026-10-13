"""Shared helpers for tests that drive the LLM gateway against a fake provider.

Helpers (import them from here; the fake provider and its stream steps live in
`tests/fake_provider.py`):

- `FakeProvider`: an `httpx.MockTransport` that answers each upstream request with the next
  scripted response, in order. Script with `reply_json(body, status=, headers=)` and
  `reply_stream(steps, status=, headers=)`; either takes `before_headers=Stall(...)` to hang
  before the response starts. `requests` logs every request (`RecordedRequest`: method, url,
  headers, json). Stream bookkeeping: `streams_opened`, `streams_closed` (distinct streams whose
  `aclose()` ran), `aclose_calls` and `open_streams`; `pending` counts the scripted responses not
  used yet. An unscripted request raises `AssertionError` inside the transport (the gateway
  answers it with a 500) and is kept in `unscripted`: the `fake_provider` fixture fails the test
  at teardown when there is one, so a gateway that sends more requests than scripted never
  passes quietly.
- Stream steps for `reply_stream`: `bytes` (one chunk), `Stall(event)` (wait until the event is
  set; `Stall()` waits forever) and `Fail(error)` (raise; `Fail()` raises `httpx.ReadError`).
  `sse_frames(text)` splits SSE text into one chunk per frame; `fixture_text(path)` reads a file
  under `tests/gateway/fixtures/`.
- `public_resolver`: a DNS stand-in that resolves every host to `PUBLIC_ADDRESS`, so the egress
  policy lets requests reach the fake provider.
- `ManualClock`: a wall clock (`wall`) and a monotonic clock (`monotonic`) that move only on
  `advance(seconds)`; pass it to the `gateway_context` factory.
- `read_gateway_spans(sessions, project_id)` / `read_gateway_span(...)`: drain the span recorder,
  then read the project's spans past row-level security (rows as dicts; the second asserts there
  is exactly one).
- `metric_delta(counter, **labels)`: snapshot a Prometheus counter; `.value` is how much it grew.
- `logged(caplog, event)`: the structlog events of that name the test logged (as dicts).

Fixtures (registered for every test directory by `tests/gateway_fixtures.py`):

- `gateway_settings`: the test settings with `credentials_keys` set.
- `fake_provider`: a fresh `FakeProvider`.
- `gateway_app`: the app started with `fake_provider` as the upstream transport and
  `public_resolver` as DNS.
- `gateway_client`: an `httpx.AsyncClient` on `gateway_app` (with the test `Origin`).
- `gateway_workspace`: a `GatewayWorkspace` made through the API by an org owner: org, project,
  an OpenAI and an Anthropic credential, one stored route (both targets, default retry and
  fallback policies) and an `spl_gw_` key; `gw` is a client that sends that key.
- `gateway_context`: an async factory for a `GatewayContext` on the workspace's project, for
  direct `execute()` calls (see `GatewayContextFactory`); teardown drains the span recorder.

Draining the recorder (the `gateway_context` teardown and `read_gateway_spans`) gives up after
`DRAIN_TIMEOUT_S` and fails the test, so a span write stuck behind a forgotten `Stall()` ends
the test instead of hanging the suite.
"""

import asyncio
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import REGISTRY, Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway import recorder
from tests.fake_provider import (
    Fail,
    FakeProvider,
    RecordedRequest,
    ScriptedStream,
    Stall,
    StreamStep,
)

FIXTURES = Path(__file__).parent / "gateway" / "fixtures"
# A public address outside every blocked range (`app.core.egress`); nothing ever connects to it.
PUBLIC_ADDRESS = "93.184.216.34"
# The longest a test waits for the span recorder's background writes.
DRAIN_TIMEOUT_S = 5.0


async def public_resolver(host: str, port: int) -> Sequence[str]:
    """Every host resolves to `PUBLIC_ADDRESS`."""
    return [PUBLIC_ADDRESS]


def fixture_text(relative: str) -> str:
    """A file under `tests/gateway/fixtures/`, such as `openai/chat_stream.sse`."""
    return (FIXTURES / relative).read_text(encoding="utf-8")


def sse_frames(sse: str) -> list[bytes]:
    """SSE text as one chunk per frame, each ending with its blank line."""
    return [f"{frame}\n\n".encode() for frame in sse.strip("\n").split("\n\n")]


class ManualClock:
    """Wall and monotonic time that move only when the test says so."""

    def __init__(self, start: datetime | None = None) -> None:
        self._wall = start or datetime.now(UTC)
        self._monotonic = time.monotonic()

    def wall(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._wall += timedelta(seconds=seconds)
        self._monotonic += seconds


async def drain_recorder() -> None:
    """Wait for the span recorder's background writes; fail after `DRAIN_TIMEOUT_S`."""
    await asyncio.wait_for(recorder.drain(), DRAIN_TIMEOUT_S)


async def read_gateway_spans(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID | str
) -> list[dict[str, Any]]:
    """The project's spans, oldest first, once every queued span has been written.

    Read with the bypass flag, like the worker: the app role sees no telemetry without a bound
    project.
    """
    await drain_recorder()
    async with sessions() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        rows = await db.execute(
            text("SELECT * FROM spans WHERE project_id = :id ORDER BY started_at, span_id"),
            {"id": str(project_id)},
        )
        return [dict(row) for row in rows.mappings()]


async def read_gateway_span(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID | str
) -> dict[str, Any]:
    """The project's one span; fails the test when there is not exactly one."""
    spans = await read_gateway_spans(sessions, project_id)
    assert len(spans) == 1, f"expected exactly one span, found {len(spans)}"
    return spans[0]


def logged(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    """The structlog events named `event` that reached the stdlib handlers during the test.

    Read through `caplog`, not `structlog.testing.capture_logs`: loggers are cached on first use,
    so capturing misses a module whose logger an earlier test already used.
    """
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == event
    ]


class MetricDelta:
    """How much one labelled counter has grown since the snapshot was taken."""

    def __init__(self, counter: Counter, labels: dict[str, str]) -> None:
        [metric] = counter.describe()
        self._sample = f"{metric.name}_total"
        self._labels = labels
        self._before = self._read()

    def _read(self) -> float:
        return REGISTRY.get_sample_value(self._sample, self._labels) or 0.0

    @property
    def value(self) -> float:
        return self._read() - self._before


def metric_delta(counter: Counter, **labels: str) -> MetricDelta:
    """Snapshot `counter` with `labels` now; read `.value` after the code under test ran."""
    return MetricDelta(counter, labels)


__all__ = [
    "DRAIN_TIMEOUT_S",
    "FIXTURES",
    "PUBLIC_ADDRESS",
    "Fail",
    "FakeProvider",
    "ManualClock",
    "MetricDelta",
    "RecordedRequest",
    "ScriptedStream",
    "Stall",
    "StreamStep",
    "drain_recorder",
    "fixture_text",
    "logged",
    "metric_delta",
    "public_resolver",
    "read_gateway_span",
    "read_gateway_spans",
    "sse_frames",
]
