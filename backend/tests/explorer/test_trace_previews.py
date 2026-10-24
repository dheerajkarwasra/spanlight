"""Trace list previews (`preview=true`) and `request_hash` on trace detail's spans.

Each preview is the root span's own payload when it has one, else the first llm span's input or
the last llm span's output; only the first 4000 characters of the payload's JSON text are read.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.explorer.trace_previews import TRACE_PREVIEWS
from app.ingest.request_hash import request_hash
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NOW = datetime.now(UTC).replace(microsecond=0)
START = NOW - timedelta(hours=2)


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


def _at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def _root(trace_id: str, **overrides: Any) -> dict[str, Any]:
    return span(
        trace_id=trace_id,
        span_id="b000000000000001",
        kind="chain",
        name="agent",
        start=_at(0),
        duration_ms=9000,
        model=None,
        provider=None,
        usage=None,
        **overrides,
    )


def _call(trace_id: str, number: int, **overrides: Any) -> dict[str, Any]:
    return span(
        trace_id=trace_id,
        span_id=f"b00000000000000{number + 1}",
        parent_span_id="b000000000000001",
        start=_at(number),
        **overrides,
    )


async def _previews(workspace: Workspace, **params: Any) -> dict[str, tuple[Any, Any]]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces", params=params
    )
    assert response.status_code == 200, response.text
    return {
        item["trace_id"]: (item["input_preview"], item["output_preview"])
        for item in response.json()["items"]
    }


async def test_previews_pick_root_payload_else_llm_spans(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    own, picked, empty, mixed = (new_trace_id() for _ in range(4))
    calls = [
        {"input": {"messages": [{"role": "user", "content": "first question"}]}},
        {"input": {"messages": [{"role": "user", "content": "second question"}]}, "output": None},
        {"input": None, "output": {"role": "assistant", "content": "final answer"}},
    ]
    await _ingest(
        client,
        workspace,
        [
            _root(own, input={"query": "plain question"}, output={"answer": "plain answer"}),
            *[_call(own, number, **payloads) for number, payloads in enumerate(calls, 1)],
            _root(picked, input=None, output=None),
            *[_call(picked, number, **payloads) for number, payloads in enumerate(calls, 1)],
            _root(empty, input=None, output=None),
            _call(empty, 1, input=None, output=None),
            # Per field: the root's own input, the llm calls' output.
            _root(mixed, input={"query": "plain question"}, output=None),
            *[_call(mixed, number, **payloads) for number, payloads in enumerate(calls, 1)],
        ],
    )

    previews = await _previews(workspace, preview="true")

    assert previews[own] == ("plain question", "plain answer")
    assert previews[picked] == ("first question", "final answer")
    assert previews[empty] == (None, None)
    assert previews[mixed] == ("plain question", "final answer")
    # Without `preview=true` the fields are null, whatever the payloads.
    assert set((await _previews(workspace)).values()) == {(None, None)}


async def test_preview_query_reads_at_most_4000_chars(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    image = "iVBORw0KGgo" + "A" * (1024 * 1024 - 1000)
    await _ingest(
        client,
        workspace,
        [span(trace_id=trace_id, start=_at(0), input={"text": image}, output="x " * 500_000)],
    )

    input_preview, output_preview = (await _previews(workspace, preview="true"))[trace_id]

    assert input_preview == "text : [binary]"
    assert output_preview is not None and len(output_preview) <= 200
    # Every payload the statement reads is cut in SQL.
    sql = str(TRACE_PREVIEWS)
    assert sql.count("left(") == sql.count("::text") == 2


async def test_span_out_has_request_hash(client: httpx.AsyncClient, workspace: Workspace) -> None:
    trace_id = new_trace_id()
    asked = {"messages": [{"role": "user", "content": "hash me"}]}
    await _ingest(
        client,
        workspace,
        [_root(trace_id, input=None, output=None), _call(trace_id, 1, input=asked)],
    )

    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"
    )

    assert response.status_code == 200, response.text
    hashes = {s["name"]: s["request_hash"] for s in response.json()["spans"]}
    assert hashes == {"agent": None, "chat gpt-4o-mini": request_hash("gpt-4o-mini", asked)}


async def test_long_chat_input_previews_last_user_message(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    chat, listed = new_trace_id(), new_trace_id()
    tool = {"type": "function", "function": {"name": "search", "description": "Find. " * 200}}
    turns = [
        {"role": "system", "content": "You are a careful agent. " * 300},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": [{"type": "text", "text": "what about refunds?"}]},
        {"role": "assistant", "content": "checking"},
    ]
    await _ingest(
        client,
        workspace,
        [
            span(trace_id=chat, start=_at(0), input={"tools": [tool] * 5, "messages": turns}),
            span(trace_id=listed, start=_at(1), input=turns),
        ],
    )

    previews = await _previews(workspace, preview="true")

    assert previews[chat][0] == "what about refunds?"
    assert previews[listed][0] == "what about refunds?"


async def test_previews_never_cross_projects(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    alice_ws = await create_workspace(await browser_factory("alice@example.com"), org_name="A")
    bob_ws = await create_workspace(await browser_factory("bob@example.com"), org_name="B")
    shared_id = new_trace_id()
    for workspace, word in ((alice_ws, "alice secret"), (bob_ws, "bob text")):
        await _ingest(
            client,
            workspace,
            [
                span(
                    trace_id=shared_id, start=_at(0), input={"query": word}, output={"answer": word}
                )
            ],
        )

    assert (await _previews(bob_ws, preview="true")) == {shared_id: ("bob text", "bob text")}
    assert (await _previews(alice_ws, preview="true")) == {
        shared_id: ("alice secret", "alice secret")
    }
