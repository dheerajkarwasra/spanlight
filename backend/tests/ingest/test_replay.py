"""Replaying spans: a changed span replaces, a replay moves only `ingested_at`, prices follow.

Ingestion has no idempotency key; it needs none, because spans upsert on their natural key
`(project_id, trace_id, span_id)` and every trace total is recomputed from the stored spans.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from tests.helpers import (
    Workspace,
    new_span_id,
    new_trace_id,
    span,
)
from tests.ingest.support import ingest_native, stored_spans, telemetry_rows

_SPAN_ROWS = text(
    "SELECT span_id, to_jsonb(s) FROM spans AS s WHERE project_id = :project_id ORDER BY span_id"
)
_TRACE_ROWS = text(
    "SELECT trace_id, to_jsonb(t) FROM traces AS t WHERE project_id = :project_id ORDER BY trace_id"
)
Table = dict[str, dict[str, Any]]


async def _trace(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> dict[str, Any]:
    """The project's one trace; its cost, which jsonb returns as a float, as a `Decimal`."""
    _, [trace] = await telemetry_rows(session_factory, project_id)
    cost = trace["cost_usd"]
    return {**trace, "cost_usd": None if cost is None else Decimal(str(cost))}


async def _tables(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> tuple[Table, Table]:
    """Every column of every span and trace of a project, keyed by span or trace id."""
    async with session_factory() as db:
        await bypass_rls(db)
        params = {"project_id": project_id}
        spans = {key: row for key, row in await db.execute(_SPAN_ROWS, params)}
        traces = {key: row for key, row in await db.execute(_TRACE_ROWS, params)}
    return spans, traces


def _changed_columns(before: Table, after: Table) -> set[str]:
    """The columns whose value differs in any row; the rows themselves must be the same."""
    assert before.keys() == after.keys()
    return {
        column
        for key in before
        for column in before[key]
        if before[key][column] != after[key][column]
    }


async def test_replaying_a_changed_span_replaces_not_adds(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, span_id = new_trace_id(), new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    first = span(trace_id=trace_id, span_id=span_id, start=start)
    changed = span(
        trace_id=trace_id,
        span_id=span_id,
        start=start,
        status="error",
        usage={"input_tokens": 400, "output_tokens": 100, "cached_tokens": 0},
    )
    assert (await ingest_native(client, workspace, [first])).status_code == 200
    before = await _trace(session_factory, workspace.project_id)
    assert (before["input_tokens"], before["output_tokens"], before["error_count"]) == (
        1000,
        500,
        0,
    )

    replayed = await ingest_native(client, workspace, [changed])

    assert replayed.json() == {"accepted": 1, "rejected": []}
    after = await _trace(session_factory, workspace.project_id)
    assert after["span_count"] == 1
    assert (after["input_tokens"], after["output_tokens"], after["error_count"]) == (400, 100, 1)
    # gpt-4o-mini at the seed rates, per million tokens: 400 * 0.15 + 100 * 0.60
    assert after["cost_usd"] == Decimal("0.00012")
    assert after["cost_usd"] != before["cost_usd"]
    [stored] = await stored_spans(session_factory)
    assert (stored.input_tokens, stored.status.value) == (400, "error")


async def test_only_ingested_at_changes_on_replay(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, root_id = new_trace_id(), new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    batch = [
        span(trace_id=trace_id, span_id=root_id, kind="chain", model=None, usage=None, start=start),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(seconds=1)),
    ]
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    spans_before, traces_before = await _tables(session_factory, workspace.project_id)

    assert (await ingest_native(client, workspace, batch)).status_code == 200
    spans_after, traces_after = await _tables(session_factory, workspace.project_id)

    assert len(spans_before) == 2
    assert _changed_columns(spans_before, spans_after) == {"ingested_at"}
    assert _changed_columns(traces_before, traces_after) == set()


async def test_in_batch_duplicate_keeps_the_later_copy(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, duplicate_id = new_trace_id(), new_span_id()
    batch = [
        span(trace_id=trace_id, span_id=duplicate_id, name="first copy"),
        span(trace_id=trace_id, span_id=duplicate_id, name="second copy"),
    ]

    body = (await ingest_native(client, workspace, batch)).json()

    assert body["accepted"] == 1
    assert [item["index"] for item in body["rejected"]] == [0]
    [stored] = await stored_spans(session_factory)
    assert stored.name == "second copy"


async def _post_override(workspace: Workspace) -> str:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": "gpt-4o-mini",
            "input_per_mtok": "100",
            "output_per_mtok": "200",
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _priced(
    session_factory: async_sessionmaker[AsyncSession], workspace: Workspace
) -> tuple[Decimal | None, str | None, Decimal | None]:
    """The stored span's cost and price version, and its trace's total cost."""
    [stored] = await stored_spans(session_factory)
    trace = await _trace(session_factory, workspace.project_id)
    return stored.cost_usd, stored.pricing_version, trace["cost_usd"]


async def test_replay_after_a_price_override_reprices(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Replay prices from the book in force at replay time; an override applies and then lifts."""
    batch = [span(trace_id=new_trace_id())]
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    seed_cost, seed_version, seed_total = await _priced(session_factory, workspace)
    assert seed_cost is not None and seed_cost == seed_total

    override_id = await _post_override(workspace)
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    cost, version, total = await _priced(session_factory, workspace)
    # 1000 input * 100 + 500 output * 200, per million tokens
    assert cost == Decimal("0.20000000")
    assert version != seed_version
    assert cost == total

    deleted = await workspace.owner.delete(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides/{override_id}"
    )
    assert deleted.status_code == 204, deleted.text
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    assert await _priced(session_factory, workspace) == (seed_cost, seed_version, seed_total)
