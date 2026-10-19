"""Trace compare: steps paired by name and order, B's change against A, and tenancy.

Failure modes pinned here: a repeated step paired with the wrong occurrence; a step only in B
dropped or put at the end; an unchanged output reported as changed (or a missing one as
unchanged); a trace of another project compared, or its existence revealed.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.explorer.trace_compare import align
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_workspace,
    new_span_id,
    new_trace_id,
    span,
)
from tests.ingest.support import ingest_native


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


def _spans(*names: str) -> list[Any]:
    return [SimpleNamespace(name=name) for name in names]


def test_align_pairs_by_name_and_occurrence_in_a_order() -> None:
    a = _spans("agent", "chat", "tool", "chat")
    b = _spans("agent", "chat", "search", "chat", "tool", "extra")

    steps = [(key, x is not None, y is not None) for key, x, y in align(a, b)]

    assert steps == [
        (("agent", 1), True, True),
        (("chat", 1), True, True),
        # Only in B: right after the last step before it that A also has ("chat" #1).
        (("search", 1), False, True),
        (("tool", 1), True, True),
        # B ran "tool" after "chat" #2: "extra" follows "tool", its predecessor in B.
        (("extra", 1), False, True),
        (("chat", 2), True, True),
    ]


def test_align_puts_a_leading_b_only_step_first_and_keeps_a_only_steps() -> None:
    steps = [key for key, _, _ in align(_spans("x", "y"), _spans("new", "y"))]
    assert steps == [("new", 1), ("x", 1), ("y", 1)]


def _run(trace_id: str, *, slow: bool, answer: str) -> list[dict[str, Any]]:
    """One agent run: the agent span, two model calls and, in the slow run, a retry tool call."""
    start = datetime.now(UTC) - timedelta(minutes=5)
    root = new_span_id()

    def child(name: str, at_ms: int, **overrides: Any) -> dict[str, Any]:
        return span(
            trace_id=trace_id,
            name=name,
            parent_span_id=root,
            start=start + timedelta(milliseconds=at_ms),
            **overrides,
        )

    spans = [
        span(
            trace_id=trace_id,
            span_id=root,
            name="answer_ticket",
            kind="agent",
            model=None,
            usage=None,
            output=None,
            start=start,
            duration_ms=900,
        ),
        child("chat", 10, duration_ms=300 if slow else 200, output={"content": answer}),
        child("chat", 400, output={"content": "same"}),
    ]
    if slow:
        spans.append(child("lookup_order", 600, kind="tool", model=None, usage=None))
    return spans


async def test_compare_reports_steps_and_deltas(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    a, b = new_trace_id(), new_trace_id()
    stored = await ingest_native(
        client,
        workspace,
        _run(a, slow=False, answer="hello") + _run(b, slow=True, answer="hello there"),
    )
    assert stored.status_code == 200, stored.text

    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces/compare", params={"a": a, "b": b}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["a"]["trace_id"], body["b"]["trace_id"]) == (a, b)
    assert body["truncated"] is False
    assert body["totals"]["span_count"] == 1
    steps = {(step["name"], step["occurrence"]): step for step in body["steps"]}
    assert list(steps) == [
        ("answer_ticket", 1),
        ("chat", 1),
        ("chat", 2),
        ("lookup_order", 1),
    ]
    agent = steps[("answer_ticket", 1)]
    assert agent["a"]["kind"] == agent["b"]["kind"] == "agent"
    assert agent["output_changed"] is None  # neither side captured an output
    changed = steps[("chat", 1)]
    assert (changed["input_changed"], changed["output_changed"]) == (False, True)
    assert changed["duration_ms"] == pytest.approx(100)
    assert changed["input_tokens"] == 0
    assert steps[("chat", 2)]["output_changed"] is False
    only_b = steps[("lookup_order", 1)]
    assert only_b["a"] is None
    assert only_b["b"]["kind"] == "tool"
    assert (only_b["duration_ms"], only_b["cost_usd"], only_b["output_changed"]) == (
        None,
        None,
        None,
    )


async def test_compare_never_reaches_another_project(
    client: httpx.AsyncClient, workspace: Workspace, browser_factory: Any
) -> None:
    stranger = await create_workspace(await browser_factory("stranger@example.com"), org_name="B")
    mine, theirs = new_trace_id(), new_trace_id()
    assert (await ingest_native(client, workspace, [span(trace_id=mine)])).status_code == 200
    assert (await ingest_native(client, stranger, [span(trace_id=theirs)])).status_code == 200
    route = f"/api/v1/projects/{workspace.project_id}/traces/compare"

    # A member of the project names a trace of another project: the same 404 as a missing trace,
    # whichever side it is on.
    for pair in ({"a": mine, "b": theirs}, {"a": theirs, "b": mine}):
        response = await workspace.owner.get(route, params=pair)
        assert response.status_code == 404, response.text
    assert (await workspace.owner.get(route, params={"a": mine, "b": mine})).status_code == 200

    # A non-member gets 404 for the project, even with ids that exist in it.
    response = await stranger.owner.get(route, params={"a": mine, "b": mine})
    assert response.status_code == 404, response.text

    # An API key of the other project, allowed to read traces, cannot use this project's route.
    key = await create_api_key(stranger.owner, stranger.project_id, scopes=["traces:read"])
    response = await client.get(route, params={"a": mine, "b": mine}, headers=bearer(key["secret"]))
    assert response.status_code == 404, response.text
