"""Retry calls and retry cost per trace, computed by the trace recompute.

Among a trace's llm spans with a request hash, cache hits and fault-injected spans left out, every
span of a hash but the last by `(started_at, span_id)` is superseded: `retry_calls` counts them
and `retry_cost_usd` sums their cost, unknown (NULL) when any of them is unpriced.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from tests.helpers import Workspace, new_trace_id, span
from tests.ingest.support import ingest_native

HASH_A = "a" * 32
HASH_B = "b" * 32
START = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)

RetryColumns = tuple[int | None, Decimal | None]


def _call(trace_id: str, seconds: int, request_hash: str | None = HASH_A, **overrides: Any) -> Any:
    """An llm span of `trace_id` starting `seconds` after START, with the given request hash."""
    body = span(trace_id=trace_id, start=START + timedelta(seconds=seconds), **overrides)
    if request_hash is not None:
        body["request_hash"] = request_hash
    return body


def _usage(input_tokens: int, output_tokens: int | None = 500) -> dict[str, Any]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cached_tokens": 0}


def _cut_stream(trace_id: str, seconds: int) -> Any:
    """A streamed call cut before its final usage: no output count, so its cost is unknown."""
    return _call(
        trace_id,
        seconds,
        usage=_usage(1000, None),
        attributes={"usage.unreported_reason": "client_disconnected"},
    )


async def _send(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await ingest_native(client, workspace, spans)
    assert response.status_code == 200, response.text
    assert response.json()["rejected"] == [], response.text


async def _retry_columns(
    session_factory: async_sessionmaker[AsyncSession], trace_id: str
) -> RetryColumns:
    async with session_factory() as db:
        await bypass_rls(db)
        row = (
            await db.execute(
                select(Trace.retry_calls, Trace.retry_cost_usd).where(Trace.trace_id == trace_id)
            )
        ).one()
    return row.retry_calls, row.retry_cost_usd


async def _span_costs(
    session_factory: async_sessionmaker[AsyncSession], *spans: dict[str, Any]
) -> list[Decimal | None]:
    """The stored cost of each given span, in the order given."""
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(select(Span.span_id, Span.cost_usd))
        costs = {span_id: cost for span_id, cost in rows}
    return [costs[sent["span_id"]] for sent in spans]


async def test_same_hash_in_two_traces_is_not_a_retry(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first, second = new_trace_id(), new_trace_id()

    await _send(client, workspace, [_call(first, 0), _call(second, 5)])

    assert await _retry_columns(session_factory, first) == (0, Decimal(0))
    assert await _retry_columns(session_factory, second) == (0, Decimal(0))


async def test_a_failed_last_span_is_still_the_survivor(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    attempts = [_call(trace_id, seconds, status="error") for seconds in (0, 5, 10)]

    await _send(client, workspace, attempts)

    first, second, _ = await _span_costs(session_factory, *attempts)
    assert first is not None and second is not None
    assert await _retry_columns(session_factory, trace_id) == (2, first + second)


async def test_a_superseded_cut_stream_makes_the_retry_cost_unknown(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    cut, retried = _cut_stream(trace_id, 0), _call(trace_id, 5)

    await _send(client, workspace, [cut, retried])

    assert (await _span_costs(session_factory, cut)) == [None]
    assert await _retry_columns(session_factory, trace_id) == (1, None)


async def test_a_priced_retry_before_an_unpriced_survivor_is_counted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    retried, survivor = _call(trace_id, 0), _cut_stream(trace_id, 5)

    await _send(client, workspace, [retried, survivor])

    [cost] = await _span_costs(session_factory, retried)
    assert cost is not None
    assert await _retry_columns(session_factory, trace_id) == (1, cost)


async def test_a_cache_hit_after_a_failure_is_excluded(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    failed = _call(trace_id, 0, status="error")
    hit = _call(trace_id, 5, attributes={"spanlight.cache": "hit"})

    await _send(client, workspace, [failed, hit])

    assert await _retry_columns(session_factory, trace_id) == (0, Decimal(0))


async def test_a_fault_injected_span_is_excluded(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    fault = _call(trace_id, 0, status="error", attributes={"spanlight.fault.scenario": "timeout"})

    await _send(client, workspace, [fault, _call(trace_id, 5)])

    assert await _retry_columns(session_factory, trace_id) == (0, Decimal(0))


async def test_spans_without_a_request_hash_are_ignored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    # Without an input the server computes no hash either.
    unhashed = [_call(trace_id, seconds, None, input=None) for seconds in (0, 5)]

    await _send(client, workspace, unhashed)

    async with session_factory() as db:
        await bypass_rls(db)
        hashes = (await db.scalars(select(Span.request_hash))).all()
    assert hashes == [None, None]
    assert await _retry_columns(session_factory, trace_id) == (0, Decimal(0))


async def test_a_trace_without_llm_spans_is_a_known_zero(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    tools = [_call(trace_id, seconds, kind="tool", model=None, usage=None) for seconds in (0, 5)]

    await _send(client, workspace, tools)

    assert await _retry_columns(session_factory, trace_id) == (0, Decimal(0))


async def test_a_trace_stored_before_the_migration_is_computed_once_a_span_is_resent(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    retried, survivor = _call(trace_id, 0), _call(trace_id, 5)
    await _send(client, workspace, [retried, survivor])
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(update(Trace).values(retry_calls=None, retry_cost_usd=None))
        await db.commit()
    assert await _retry_columns(session_factory, trace_id) == (None, None)

    await _send(client, workspace, [survivor])

    [cost] = await _span_costs(session_factory, retried)
    assert await _retry_columns(session_factory, trace_id) == (1, cost)


async def test_replay_is_idempotent(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    batch = [_call(trace_id, 0), _call(trace_id, 5), _call(trace_id, 10, HASH_B)]

    await _send(client, workspace, batch)
    first = await _retry_columns(session_factory, trace_id)
    await _send(client, workspace, batch)

    assert first[0] == 1
    assert await _retry_columns(session_factory, trace_id) == first


async def test_out_of_order_arrival_converges(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    earlier = _call(trace_id, 0, usage=_usage(1000))
    later = _call(trace_id, 5, usage=_usage(4000))

    await _send(client, workspace, [later])
    assert await _retry_columns(session_factory, trace_id) == (0, Decimal(0))
    await _send(client, workspace, [earlier])

    earlier_cost, later_cost = await _span_costs(session_factory, earlier, later)
    assert earlier_cost != later_cost
    assert await _retry_columns(session_factory, trace_id) == (1, earlier_cost)


async def test_three_retries_sum_the_first_two(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    attempts = [
        _call(trace_id, seconds, usage=_usage(tokens))
        for seconds, tokens in ((0, 1000), (5, 2000), (10, 3000))
    ]

    await _send(client, workspace, attempts)

    first, second, _ = await _span_costs(session_factory, *attempts)
    assert first is not None and second is not None
    assert await _retry_columns(session_factory, trace_id) == (2, first + second)


async def test_a_retry_cost_too_large_to_store_is_unknown(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Each call costs about $600,000 and fits; the two superseded ones, about $1,200,000, do not
    # fit `traces.retry_cost_usd` (numeric(14, 8)), which must not fail the batch.
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": "huge-model",
            "input_per_mtok": "999999.999999",
            "output_per_mtok": "999999.999999",
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    trace_id = new_trace_id()
    attempts = [
        _call(trace_id, seconds, model="huge-model", usage=_usage(600_000, 0))
        for seconds in (0, 5, 10)
    ]

    await _send(client, workspace, attempts)

    assert await _retry_columns(session_factory, trace_id) == (2, None)


async def test_a_start_time_tie_is_broken_by_span_id(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    smaller = _call(trace_id, 0, span_id="0000000000000001", usage=_usage(1000))
    larger = _call(trace_id, 0, span_id="00000000000000ff", usage=_usage(4000))

    # Sent larger first: the order of arrival must not decide the survivor.
    await _send(client, workspace, [larger, smaller])

    smaller_cost, larger_cost = await _span_costs(session_factory, smaller, larger)
    assert smaller_cost != larger_cost
    assert await _retry_columns(session_factory, trace_id) == (1, smaller_cost)


async def test_an_sdk_span_around_a_gateway_span_of_the_same_request_is_not_a_retry(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A caller who joins an SDK `wrap_*` span to the gateway's span by hand
    # (`x-spanlight-trace-id` / `-parent-span-id`) gets two llm spans with one request hash. The
    # outer one is a usage rollup of the inner (migration 0900), so it is not a retry of it.
    trace_id = new_trace_id()
    sdk = _call(trace_id, 0, span_id="00000000000000a1", duration_ms=2000)
    gateway = _call(
        trace_id,
        1,
        span_id="00000000000000b2",
        parent_span_id=sdk["span_id"],
        attributes={"spanlight.gateway.status": 200},
    )

    await _send(client, workspace, [sdk, gateway])

    assert await _retry_columns(session_factory, trace_id) == (0, 0)
