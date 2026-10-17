"""A 2xx from ingestion means the rows are committed; a refused or limited one stores nothing."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests.conftest import admin_database_url
from tests.helpers import Workspace, bearer, create_api_key, new_span_id, new_trace_id, span
from tests.ingest.support import ingest_native, stored_spans


@pytest.fixture
async def fresh_engine() -> AsyncIterator[AsyncEngine]:
    """An engine on the admin URL, apart from the app's pool."""
    engine = create_async_engine(admin_database_url(), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_200_means_the_rows_are_visible_on_a_fresh_connection(
    client: httpx.AsyncClient, workspace: Workspace, fresh_engine: AsyncEngine
) -> None:
    trace_id = new_trace_id()
    root_id = new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    batch = [
        span(trace_id=trace_id, span_id=root_id, start=start),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(seconds=1)),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(seconds=2)),
    ]

    response = await ingest_native(client, workspace, batch)
    assert response.json() == {"accepted": 3, "rejected": []}

    # The admin role bypasses row-level security, and this connection is not the request's.
    async with fresh_engine.connect() as connection:
        spans = (
            (
                await connection.execute(
                    text("SELECT span_id FROM spans WHERE trace_id = :t ORDER BY started_at"),
                    {"t": trace_id},
                )
            )
            .scalars()
            .all()
        )
        trace = (
            await connection.execute(
                text(
                    "SELECT span_count, input_tokens, output_tokens FROM traces WHERE trace_id = :t"
                ),
                {"t": trace_id},
            )
        ).one()
    assert len(spans) == 3 and spans[0] == root_id
    # span() carries 1000 input and 500 output tokens; the root repeats its children's usage,
    # so it is a usage rollup and only the children are summed.
    assert tuple(trace) == (3, 2000, 1000)


async def test_a_refused_ingest_consumes_no_tokens_and_stores_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    reader = await create_api_key(workspace.owner, workspace.project_id, scopes=["traces:read"])
    batch = [span(trace_id=new_trace_id())]

    # More refusals than the burst of 100: if each spent a token, the later ones would be 429s.
    statuses = {
        (
            await client.post("/v1/traces", json={"spans": batch}, headers=bearer(reader["secret"]))
        ).status_code
        for _ in range(130)
    }

    assert statuses == {403}
    async with session_factory() as db:
        buckets = (
            await db.execute(
                text("SELECT count(*) FROM rate_limit_buckets WHERE key = :k"),
                {"k": f"ingest:key:{reader['id']}"},
            )
        ).scalar_one()
    assert buckets == 0
    assert await stored_spans(session_factory) == []

    # The control: a key that may ingest is not affected.
    allowed = await ingest_native(client, workspace, batch)
    assert allowed.json() == {"accepted": 1, "rejected": []}


async def test_rate_limited_ingest_stores_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admitted = await _send_one_span_batches(client, workspace, 3, expect=200)
    # Empty the key's bucket and hold its refill clock an hour ahead, so the outcome does not
    # depend on how fast the requests run (the key refills 50 tokens a second).
    async with session_factory() as db, db.begin():
        emptied = await db.execute(
            text(
                "UPDATE rate_limit_buckets SET tokens = 0, updated_at = now() + interval '1 hour' "
                "WHERE key LIKE 'ingest:key:%'"
            )
        )
    assert getattr(emptied, "rowcount", 0) == 1

    limited = await _send_one_span_batches(client, workspace, 3, expect=429)

    stored_traces = {stored.trace_id for stored in await stored_spans(session_factory)}
    assert stored_traces == set(admitted)
    assert not stored_traces & set(limited)


async def _send_one_span_batches(
    client: httpx.AsyncClient, workspace: Workspace, count: int, *, expect: int
) -> list[str]:
    """`count` requests of one span in a trace of its own; the trace ids, all answered `expect`."""
    trace_ids: list[str] = []
    for _ in range(count):
        trace_id = new_trace_id()
        response = await ingest_native(client, workspace, [span(trace_id=trace_id)])
        assert response.status_code == expect, response.text
        trace_ids.append(trace_id)
    return trace_ids
