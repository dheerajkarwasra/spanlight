"""Trace metadata on the native path: validation, redaction, merge, storage and the counter."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import FEATURE_USED
from app.db.models import Trace
from app.db.rls import bypass_rls
from tests.gateway_support import metric_delta
from tests.helpers import Workspace, new_trace_id, span
from tests.ingest.support import ingest_native


def _span(trace_id: str, metadata: Any, *, offset_s: float = 0) -> dict[str, Any]:
    start = datetime.now(UTC) - timedelta(minutes=10) + timedelta(seconds=offset_s)
    return span(trace_id=trace_id, start=start, trace={"name": "answer", "metadata": metadata})


async def _metadata(
    session_factory: async_sessionmaker[AsyncSession], trace_id: str
) -> dict[str, str]:
    async with session_factory() as db:
        await bypass_rls(db)
        stored = await db.scalar(select(Trace.metadata_).where(Trace.trace_id == trace_id))
        assert stored is not None
        return dict(stored)


async def test_native_metadata_is_stored_and_merged_within_a_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    response = await ingest_native(
        client,
        workspace,
        [
            _span(trace_id, {"customer_id": "acme", "plan": "free", "retries": 3}),
            _span(trace_id, {"customer_id": "globex", "beta": True}, offset_s=1),
        ],
    )

    assert response.json() == {"accepted": 2, "rejected": []}
    assert await _metadata(session_factory, trace_id) == {
        "beta": "true",
        "customer_id": "globex",  # the later span wins per key
        "plan": "free",
        "retries": "3",
    }


async def test_a_trace_without_metadata_stores_an_empty_object(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    await ingest_native(client, workspace, [span(trace_id=trace_id)])

    assert await _metadata(session_factory, trace_id) == {}


async def test_native_metadata_merges_across_batches_and_caps_at_32_in_lexical_order(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    first = {f"k{number:02d}": "one" for number in range(30)}
    await ingest_native(client, workspace, [_span(trace_id, first)])
    # Two places left: of the four new keys only `a0` and `k99` fit, in lexical order.
    second = {"k00": "two", "zz": "new", "a0": "new", "k99": "new", "m1": "new"}
    await ingest_native(client, workspace, [_span(trace_id, second, offset_s=1)])
    # Full: an existing key is still updatable, a new one is not added.
    third = {"k01": "three", "b0": "new"}
    await ingest_native(client, workspace, [_span(trace_id, third, offset_s=2)])

    stored = await _metadata(session_factory, trace_id)
    assert len(stored) == 32
    assert stored["k00"] == "two"
    assert stored["k01"] == "three"
    assert stored["a0"] == stored["k99"] == "new"
    assert {"zz", "m1", "b0"}.isdisjoint(stored)


async def test_native_rejects_invalid_metadata_per_span_with_the_field_path(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    batch = [
        _span(new_trace_id(), {f"k{number:02d}": "v" for number in range(33)}),
        _span(new_trace_id(), {"Customer": "acme"}),
        _span(new_trace_id(), {"plan": ""}),
        _span(new_trace_id(), {"team": {"name": "core"}}),
        _span(new_trace_id(), ["customer_id", "acme"]),
        _span(trace_id, {"customer_id": "acme", "dropped": None}),
    ]

    response = await ingest_native(client, workspace, batch)

    body = response.json()
    assert body["accepted"] == 1
    assert [(item["index"], item["reason"]) for item in body["rejected"]] == [
        (0, "trace.metadata: at most 32 keys"),
        (1, "trace.metadata.Customer: key must match ^[a-z0-9_.-]{1,64}$"),
        (2, "trace.metadata.plan: value must be 1 to 256 characters"),
        (3, "trace.metadata.team: value must be a string, number or boolean"),
        (4, "trace.metadata: must be an object"),
    ]
    assert await _metadata(session_factory, trace_id) == {"customer_id": "acme"}


async def test_native_rejects_metadata_that_is_not_an_object_with_the_contract_text(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    batch = [_span(new_trace_id(), value) for value in (["a", "b"], "acme", 42, True)]

    response = await ingest_native(client, workspace, batch)

    body = response.json()
    assert body["accepted"] == 0
    assert [item["reason"] for item in body["rejected"]] == [
        "trace.metadata: must be an object"
    ] * 4


async def test_replay_leaves_metadata_unchanged(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    batch = [
        _span(trace_id, {f"k{number:02d}": "one" for number in range(31)}),
        _span(trace_id, {"k00": "two", "z1": "new", "a1": "new"}, offset_s=1),
    ]

    await ingest_native(client, workspace, batch)
    first = await _metadata(session_factory, trace_id)
    await ingest_native(client, workspace, batch)

    assert await _metadata(session_factory, trace_id) == first
    assert len(first) == 32
    assert first["k00"] == "two"
    assert "a1" in first and "z1" not in first


async def test_values_are_redacted_and_kept_under_their_key(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    metadata = {
        "card": "4242 4242 4242 4242",
        "key": "sk-ant-api03-abcdefghijklmnop_QRS",
        "customer_id": "acme",
    }

    response = await ingest_native(client, workspace, [_span(trace_id, metadata)])

    assert response.json() == {"accepted": 1, "rejected": []}
    assert await _metadata(session_factory, trace_id) == {
        "card": "[REDACTED]",
        "key": "[REDACTED]",
        "customer_id": "acme",
    }


async def test_metadata_set_feature_counter_increments_once_per_batch(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    counted = metric_delta(FEATURE_USED, feature="metadata_set")
    trace_id = new_trace_id()

    await ingest_native(
        client,
        workspace,
        [_span(trace_id, {"customer_id": "acme"}), _span(new_trace_id(), {"plan": "pro"})],
    )
    assert counted.value == 1

    await ingest_native(client, workspace, [span(trace_id=new_trace_id())])
    await ingest_native(client, workspace, [_span(new_trace_id(), {})])
    await ingest_native(client, workspace, [_span(new_trace_id(), {"Bad": "key"})])
    assert counted.value == 1
