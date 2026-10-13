"""Batches ingested at the same moment converge on the rows one ingest leaves, and never deadlock.

The requests go through the in-process ASGI client with the app's real connection pool, so
every batch runs in its own transaction on its own connection. A real server interleaves them
more finely, but the row locks that serialize them are the same.

The no-deadlock tests send 40 batches at once. On a loaded host some of them can wait longer
than the pool timeout for a connection, and the api then sheds them with a 503 by design
(`db_pool_timeout`). That is not a deadlock: Postgres cancels one side of a deadlock, which
reaches the client as a 500. So those tests resend a shed batch, as a client would, and fail on
any 500 or on a 503 that was not a pool timeout.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest import store
from tests.gateway_support import logged
from tests.helpers import (
    Workspace,
    new_span_id,
    new_trace_id,
    span,
)
from tests.ingest.support import another_project, ingest_native, telemetry_rows

CONCURRENT_BATCHES = 10
ROUNDS = 20
TRACES_PER_ROUND = 100
PAUSE_BETWEEN_TRACES = 0.02  # seconds a slowed trace upsert waits after each trace
SHED_ATTEMPTS = 5  # sends of one batch while the api sheds it with a 503


class _Sender:
    """Ingests batches, sending a batch again after a 503, and counts the 503s it saw."""

    def __init__(self, client: httpx.AsyncClient, workspace: Workspace) -> None:
        self._client = client
        self._workspace = workspace
        self.shed = 0

    async def ingest(self, batch: list[dict[str, Any]]) -> httpx.Response:
        for _ in range(SHED_ATTEMPTS - 1):
            response = await ingest_native(self._client, self._workspace, batch)
            if response.status_code != 503:
                return response
            self.shed += 1
            await asyncio.sleep(float(response.headers["Retry-After"]))
        return await ingest_native(self._client, self._workspace, batch)


def _assert_only_pool_timeouts_shed(sender: _Sender, caplog: pytest.LogCaptureFixture) -> None:
    """Every 503 was a pool timeout (load shedding), and nothing failed unexpectedly.

    Read from the log records the app really emitted: a deadlock surfaces as an
    `unhandled_exception` whose traceback names it.
    """
    assert logged(caplog, "unhandled_exception") == []
    assert logged(caplog, "db_statement_timeout") == []
    assert len(logged(caplog, "db_pool_timeout")) == sender.shed
    assert "deadlock" not in caplog.text.lower()


def _twenty_spans_in_three_traces() -> list[dict[str, Any]]:
    start = datetime.now(UTC) - timedelta(minutes=5)
    traces = [new_trace_id() for _ in range(3)]
    return [
        span(trace_id=traces[index % 3], start=start + timedelta(milliseconds=index))
        for index in range(20)
    ]


async def test_ten_concurrent_identical_batches_converge(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    batch = _twenty_spans_in_three_traces()
    control = await another_project(workspace, "Single ingest")
    assert (await ingest_native(client, control, batch)).status_code == 200

    responses = await asyncio.gather(
        *(ingest_native(client, workspace, batch) for _ in range(CONCURRENT_BATCHES))
    )

    assert [response.status_code for response in responses] == [200] * CONCURRENT_BATCHES
    assert [response.json() for response in responses] == [{"accepted": 20, "rejected": []}] * 10
    spans, traces = await telemetry_rows(session_factory, workspace.project_id)
    assert (len(spans), len(traces)) == (20, 3)
    assert sorted(trace["span_count"] for trace in traces) == [6, 7, 7]
    # The same rows a lone ingest of the batch leaves (`telemetry_rows` omits `ingested_at`).
    assert (spans, traces) == await telemetry_rows(session_factory, control.project_id)


async def test_concurrent_batches_on_the_same_trace_in_opposite_order_do_not_deadlock(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    start = datetime.now(UTC) - timedelta(minutes=5)
    batches = []
    for _ in range(ROUNDS):
        trace_ids = sorted(new_trace_id() for _ in range(TRACES_PER_ROUND))
        # Disjoint spans of the same traces; B lists the traces the other way round. Locking
        # them in the order listed would deadlock A against B.
        batches.append([span(trace_id=trace_id, start=start) for trace_id in trace_ids])
        batches.append([span(trace_id=trace_id, start=start) for trace_id in reversed(trace_ids)])

    sender = _Sender(client, workspace)
    responses = await asyncio.gather(*(sender.ingest(batch) for batch in batches))

    assert [response.status_code for response in responses] == [200] * len(batches)
    _assert_only_pool_timeouts_shed(sender, caplog)
    spans, traces = await telemetry_rows(session_factory, workspace.project_id)
    assert (len(spans), len(traces)) == (2 * ROUNDS * TRACES_PER_ROUND, ROUNDS * TRACES_PER_ROUND)
    assert {trace["span_count"] for trace in traces} == {2}  # the union of A's and B's spans


async def test_opposite_order_batches_do_not_deadlock_when_each_trace_lock_takes_time(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real guard for lock order; the test above is a smoke test.

    The race made certain: the trace upsert takes its row locks one trace at a time.

    On a fast database two statements rarely overlap, so a lock-order bug could pass the test
    above. Here each trace is locked by a statement of its own and the request then yields, so
    the opposite-order batch always runs in between. The traces must be taken in sorted order
    (unsorted, the two batches wait for each other and Postgres cancels one of them).
    """
    caplog.set_level(logging.WARNING)
    original = store._upsert_traces

    async def one_trace_at_a_time(
        session: AsyncSession, project_id: uuid.UUID, traces: Sequence[Any]
    ) -> None:
        for trace in traces:
            await original(session, project_id, [trace])
            await asyncio.sleep(PAUSE_BETWEEN_TRACES)

    monkeypatch.setattr(store, "_upsert_traces", one_trace_at_a_time)
    start = datetime.now(UTC) - timedelta(minutes=5)
    batches = []
    for _ in range(ROUNDS):
        trace_ids = sorted(new_trace_id() for _ in range(3))
        batches.append([span(trace_id=trace_id, start=start) for trace_id in trace_ids])
        batches.append([span(trace_id=trace_id, start=start) for trace_id in reversed(trace_ids)])

    sender = _Sender(client, workspace)
    responses = await asyncio.gather(*(sender.ingest(batch) for batch in batches))

    assert [response.status_code for response in responses] == [200] * len(batches)
    _assert_only_pool_timeouts_shed(sender, caplog)
    _, traces = await telemetry_rows(session_factory, workspace.project_id)
    assert {trace["span_count"] for trace in traces} == {2}


async def test_concurrent_batches_with_overlapping_spans_keep_one_row_per_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two writers racing on the same span key end with one row, not a unique violation."""
    trace_id, span_id = new_trace_id(), new_span_id()
    batches = [
        [
            span(
                trace_id=trace_id,
                span_id=span_id,
                usage={"input_tokens": tokens, "output_tokens": 1},
            )
        ]
        for tokens in range(1, 11)
    ]

    responses = await asyncio.gather(*(ingest_native(client, workspace, b) for b in batches))

    assert [response.status_code for response in responses] == [200] * 10
    spans, [trace] = await telemetry_rows(session_factory, workspace.project_id)
    assert len(spans) == 1
    assert trace["span_count"] == 1
    assert trace["input_tokens"] == spans[0]["input_tokens"]  # the totals match the survivor
