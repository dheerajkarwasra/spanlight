"""Usage rollups: a span whose usage repeats a descendant's usage is flagged and left out of totals.

The flag (`spans.usage_rollup`, Bet 4 spec §5) is derived from the stored rows alone: a span
carries usage when either token count is set, and it is a rollup when any descendant in the same
trace carries usage too. Every batch recomputes its traces, so the flags converge whatever the
arrival order; the trace's token and cost sums skip rollups while its span and error counts keep
them.
"""

import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from tests.helpers import Workspace, new_trace_id, span
from tests.ingest.support import ingest_native, telemetry_rows

SessionFactory = async_sessionmaker[AsyncSession]

# gpt-4o-mini at the seeded prices: 1000/500 tokens is the `span()` default.
PARENT_COST = Decimal("0.00045")
CHILD_COST = Decimal("0.00012")


def _id(label: str) -> str:
    return label * 16


def _usage(input_tokens: int | None, output_tokens: int | None) -> dict[str, Any]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _span(trace_id: str, label: str, parent: str | None = None, **overrides: Any) -> Any:
    """An llm span `label` of `trace_id` under `parent`; usage 1000/500 unless overridden."""
    body = span(trace_id=trace_id, span_id=_id(label), **overrides)
    body["parent_span_id"] = None if parent is None else _id(parent)
    return body


def _bare(trace_id: str, label: str, parent: str | None, kind: str) -> Any:
    """A span of the SDK's `@trace` kind: no usage, no model, nothing to price."""
    return _span(trace_id, label, parent, kind=kind, usage=None, provider=None, model=None)


def _child(trace_id: str, label: str, parent: str, **overrides: Any) -> Any:
    """A priced child with 400/100 tokens, distinguishable from its parent in the totals."""
    return _span(trace_id, label, parent, usage=_usage(400, 100), **overrides)


async def _send(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> Any:
    response = await ingest_native(client, workspace, spans)
    assert response.status_code == 200, response.text
    return response.json()


async def _flags(session_factory: SessionFactory) -> dict[str, bool]:
    """Every stored span's flag, keyed by its one-letter label."""
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(select(Span.span_id, Span.usage_rollup))
    return {span_id[0]: flag for span_id, flag in rows}


async def _totals(session_factory: SessionFactory, trace_id: str) -> dict[str, Any]:
    async with session_factory() as db:
        await bypass_rls(db)
        row = (
            await db.execute(
                select(
                    Trace.span_count,
                    Trace.error_count,
                    Trace.input_tokens,
                    Trace.output_tokens,
                    Trace.cost_usd,
                    Trace.has_unpriced,
                ).where(Trace.trace_id == trace_id)
            )
        ).one()
    return dict(row._mapping)


def _billable(tokens: tuple[int, int], cost: Decimal | None, **counts: int) -> dict[str, Any]:
    return {
        "span_count": counts.get("spans", 2),
        "error_count": counts.get("errors", 0),
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "cost_usd": cost,
        "has_unpriced": False,
    }


async def test_chain_of_three_flags_the_top_two(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    chain = [_span(trace_id, "a"), _span(trace_id, "b", "a"), _child(trace_id, "c", "b")]

    await _send(client, workspace, chain)

    assert await _flags(session_factory) == {"a": True, "b": True, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST, spans=3)


async def test_siblings_flag_only_the_parent(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [_span(trace_id, "a"), _child(trace_id, "b", "a"), _child(trace_id, "c", "a")]

    await _send(client, workspace, tree)

    assert await _flags(session_factory) == {"a": True, "b": False, "c": False}
    assert await _totals(session_factory, trace_id) == _billable(
        (800, 200), CHILD_COST * 2, spans=3
    )


async def test_descendant_through_a_span_without_usage(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [
        _span(trace_id, "a"),
        _bare(trace_id, "b", "a", "chain"),
        _child(trace_id, "c", "b"),
    ]

    await _send(client, workspace, tree)

    assert await _flags(session_factory) == {"a": True, "b": False, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST, spans=3)


async def test_child_in_a_later_batch_flags_the_parent(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()

    await _send(client, workspace, [_span(trace_id, "a")])
    assert await _flags(session_factory) == {"a": False}
    assert await _totals(session_factory, trace_id) == _billable((1000, 500), PARENT_COST, spans=1)

    await _send(client, workspace, [_child(trace_id, "c", "a")])
    assert await _flags(session_factory) == {"a": True, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST)


async def test_parent_after_child_is_flagged_on_insert(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()

    await _send(client, workspace, [_child(trace_id, "c", "a")])
    assert await _flags(session_factory) == {"c": False}

    await _send(client, workspace, [_span(trace_id, "a")])
    assert await _flags(session_factory) == {"a": True, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST)


async def test_child_with_only_input_tokens_makes_the_parent_a_rollup(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [_span(trace_id, "a"), _span(trace_id, "c", "a", usage=_usage(400, None))]

    await _send(client, workspace, tree)

    assert await _flags(session_factory) == {"a": True, "c": False}
    totals = await _totals(session_factory, trace_id)
    # The parent's output count is not summed in the child's place; the child is unpriced.
    assert (totals["input_tokens"], totals["output_tokens"]) == (400, 0)
    assert (totals["cost_usd"], totals["has_unpriced"]) == (None, True)


async def test_orphan_child_is_billable(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()

    await _send(client, workspace, [_child(trace_id, "c", "f")])

    assert await _flags(session_factory) == {"c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST, spans=1)


async def test_parent_id_in_another_trace_is_no_relation(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    parent_trace, child_trace = new_trace_id(), new_trace_id()

    await _send(client, workspace, [_span(parent_trace, "a"), _child(child_trace, "c", "a")])

    assert await _flags(session_factory) == {"a": False, "c": False}
    assert await _totals(session_factory, parent_trace) == _billable(
        (1000, 500), PARENT_COST, spans=1
    )


async def test_sdk_trace_spans_without_usage_are_never_rollups(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [
        _bare(trace_id, "a", None, "chain"),
        _bare(trace_id, "b", "a", "other"),
        _child(trace_id, "c", "b"),
        _child(trace_id, "d", "a"),
    ]

    await _send(client, workspace, tree)

    assert await _flags(session_factory) == {"a": False, "b": False, "c": False, "d": False}
    assert await _totals(session_factory, trace_id) == _billable(
        (800, 200), CHILD_COST * 2, spans=4
    )


async def test_gateway_owned_child_still_flags_sdk_parent(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    chain = [_span(trace_id, "a"), _span(trace_id, "b", "a"), _child(trace_id, "c", "b")]
    await _send(client, workspace, chain)

    # `b` and `c` become the gateway's (a key id on the row) and every flag is reset, as rows
    # stored before the upgrade read: a native batch never overwrites a gateway-owned span, but
    # the derived flag is not client data, so the recompute still writes it on `b`.
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            update(Span)
            .where(Span.span_id.in_([_id("b"), _id("c")]))
            .values(source_key_id=uuid.uuid4())
        )
        await db.execute(update(Span).values(usage_rollup=False))
        await db.commit()

    await _send(client, workspace, [_span(trace_id, "a")])

    assert await _flags(session_factory) == {"a": True, "b": True, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST, spans=3)


async def test_replay_leaves_flags_and_totals_identical(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    chain = [_span(trace_id, "a"), _span(trace_id, "b", "a"), _child(trace_id, "c", "b")]

    await _send(client, workspace, chain)
    before = await telemetry_rows(session_factory, workspace.project_id)
    await _send(client, workspace, list(reversed(chain)))

    assert await telemetry_rows(session_factory, workspace.project_id) == before
    assert await _flags(session_factory) == {"a": True, "b": True, "c": False}


async def test_cycle_is_capped_and_both_flagged(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()

    await _send(client, workspace, [_span(trace_id, "a", "b"), _span(trace_id, "b", "a")])

    assert await _flags(session_factory) == {"a": True, "b": True}
    totals = await _totals(session_factory, trace_id)
    assert (totals["span_count"], totals["input_tokens"], totals["output_tokens"]) == (2, 0, 0)


async def test_rejected_child_does_not_flag_parent(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    bad_child = _span(trace_id, "c", "a", usage={"input_tokens": "lots", "output_tokens": 100})

    body = await _send(client, workspace, [_span(trace_id, "a"), bad_child])

    assert [rejected["span_id"] for rejected in body["rejected"]] == [_id("c")]
    assert await _flags(session_factory) == {"a": False}
    assert await _totals(session_factory, trace_id) == _billable((1000, 500), PARENT_COST, spans=1)


async def test_trace_totals_and_has_unpriced_exclude_rollups(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [_span(trace_id, "a", model="mystery-model"), _child(trace_id, "c", "a")]

    await _send(client, workspace, tree)

    assert await _flags(session_factory) == {"a": True, "c": False}
    assert await _totals(session_factory, trace_id) == _billable((400, 100), CHILD_COST)


async def test_span_and_error_counts_include_rollups(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    tree = [_span(trace_id, "a", status="error"), _child(trace_id, "c", "a")]

    await _send(client, workspace, tree)

    assert await _totals(session_factory, trace_id) == _billable(
        (400, 100), CHILD_COST, spans=2, errors=1
    )
