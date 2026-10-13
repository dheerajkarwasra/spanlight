"""Spans that arrive out of order merge into one trace; tenants never share a trace."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import Browser, Workspace, create_workspace, new_span_id, new_trace_id, span
from tests.ingest.support import ingest_native, telemetry_rows


def _time(value: str) -> datetime:
    """A timestamp read through `to_jsonb`, which drops trailing zeros of the fraction."""
    return datetime.fromisoformat(value)


async def test_child_before_parent_and_earlier_span_later(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, parent_id, child_id = new_trace_id(), new_span_id(), new_span_id()
    started = datetime.now(UTC) - timedelta(minutes=5)
    child = span(
        trace_id=trace_id,
        span_id=child_id,
        parent_span_id=parent_id,
        name="child",
        start=started + timedelta(seconds=2),
    )
    parent = span(trace_id=trace_id, span_id=parent_id, name="parent", start=started)

    assert (await ingest_native(client, workspace, [child])).status_code == 200
    [alone] = (await telemetry_rows(session_factory, workspace.project_id))[1]
    assert _time(alone["started_at"]) == started + timedelta(seconds=2)
    assert (await ingest_native(client, workspace, [parent])).status_code == 200

    [trace] = (await telemetry_rows(session_factory, workspace.project_id))[1]
    assert trace["span_count"] == 2
    # The later batch holds the earlier span: the trace starts when its first span does.
    assert _time(trace["started_at"]) == started
    assert _time(trace["ended_at"]) == started + timedelta(seconds=2, milliseconds=100)
    shown = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}")
    assert shown.status_code == 200, shown.text
    assert [(item["name"], item["parent_span_id"]) for item in shown.json()["spans"]] == [
        ("parent", None),
        ("child", parent_id),
    ]


async def _tenant(browser_factory: Any, label: str) -> Workspace:
    owner: Browser = await browser_factory(f"{label}@example.com")
    return await create_workspace(owner, org_name=f"Org {label}")


async def test_two_tenants_with_the_same_trace_id_do_not_merge(
    client: httpx.AsyncClient,
    browser_factory: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The same trace id and even the same span id, sent at the same moment, stay two traces."""
    alice, bob = await _tenant(browser_factory, "alice"), await _tenant(browser_factory, "bob")
    trace_id, span_id = new_trace_id(), new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=5)

    def shared(tokens: int, name: str) -> dict[str, Any]:
        return span(
            trace_id=trace_id,
            span_id=span_id,
            start=start,
            usage={"input_tokens": tokens, "output_tokens": 1},
            trace={"name": name},
        )

    sent = await asyncio.gather(
        ingest_native(client, alice, [shared(1000, "alice")]),
        ingest_native(client, bob, [shared(30, "bob")]),
    )

    assert [response.json()["accepted"] for response in sent] == [1, 1]
    for workspace, name, tokens in ((alice, "alice", 1000), (bob, "bob", 30)):
        spans, [trace] = await telemetry_rows(session_factory, workspace.project_id)
        assert len(spans) == 1
        assert (trace["name"], trace["span_count"], trace["input_tokens"]) == (name, 1, tokens)
        listed = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces")
        assert [(item["trace_id"], item["name"]) for item in listed.json()["items"]] == [
            (trace_id, name)
        ]
        shown = await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"
        )
        assert [item["input_tokens"] for item in shown.json()["spans"]] == [tokens]
