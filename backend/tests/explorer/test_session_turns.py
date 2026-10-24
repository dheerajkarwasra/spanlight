"""`GET /sessions/{session_id}/turns`: each trace of a session with the spans its turn shows."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.explorer import trace_queries
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
START = NOW - timedelta(hours=4)


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


def _turns_url(workspace: Workspace, session_id: str) -> str:
    return f"/api/v1/projects/{workspace.project_id}/sessions/{session_id}/turns"


async def test_session_turns_returns_conversation_spans_per_trace(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    agent, single = new_trace_id(), new_trace_id()
    session = {"session_id": "chat-1", "environment": "prod"}
    await _ingest(
        client,
        workspace,
        [
            # An agent turn: root, three llm calls, two failed tools.
            span(
                trace_id=agent,
                span_id="a000000000000001",
                kind="chain",
                name="agent",
                start=_at(0),
                duration_ms=9000,
                input="plain question",
                output="plain answer",
                model=None,
                provider=None,
                usage=None,
                trace={**session, "name": "agent"},
            ),
            span(
                trace_id=agent,
                span_id="a000000000000002",
                parent_span_id="a000000000000001",
                name="first call",
                start=_at(1),
                input={"messages": [{"role": "user", "content": "the question"}]},
                output={"role": "assistant", "content": "calling a tool"},
            ),
            span(
                trace_id=agent,
                span_id="a000000000000003",
                parent_span_id="a000000000000001",
                kind="tool",
                name="search",
                status="error",
                status_message="search timed out",
                start=_at(2),
                model=None,
                provider=None,
                usage=None,
            ),
            span(
                trace_id=agent,
                span_id="a000000000000004",
                parent_span_id="a000000000000001",
                name="middle call",
                start=_at(3),
            ),
            span(
                trace_id=agent,
                span_id="a000000000000005",
                parent_span_id="a000000000000001",
                kind="tool",
                name="search again",
                status="error",
                status_message="later failure",
                start=_at(4),
                model=None,
                provider=None,
                usage=None,
            ),
            span(
                trace_id=agent,
                span_id="a000000000000006",
                parent_span_id="a000000000000001",
                name="final call",
                start=_at(5),
                input=None,
                output={"role": "assistant", "content": "the answer"},
            ),
            # A one-span turn: the root is also the only llm call, returned once.
            span(trace_id=single, start=_at(60), trace={**session, "name": "single"}),
        ],
    )

    response = await workspace.owner.get(_turns_url(workspace, "chat-1"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] is None
    newest, oldest = body["items"]
    assert newest["trace"]["trace_id"] == single and len(newest["spans"]) == 1
    assert oldest["trace"]["trace_id"] == agent
    # Every tool span counts, also those the turn's spans leave out.
    assert (newest["tool_calls"], oldest["tool_calls"]) == (0, 2)
    assert [s["name"] for s in oldest["spans"]] == ["agent", "first call", "search", "final call"]
    assert all(s["payload_omitted"] is False for s in oldest["spans"])
    assert oldest["spans"][0]["input"] == "plain question"
    assert oldest["spans"][3]["output"] == {"role": "assistant", "content": "the answer"}
    # The summary is the traces list item itself.
    listed = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/traces", params={"session_id": "chat-1"}
        )
    ).json()["items"]
    assert [item["trace"] for item in body["items"]] == listed
    assert oldest["trace"]["error_message"] == "search timed out"


async def test_session_turns_pages_like_trace_list(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    ids = [new_trace_id() for _ in range(3)]
    await _ingest(
        client,
        workspace,
        [
            *[
                span(trace_id=trace_id, start=_at(index * 60), trace={"session_id": "paged"})
                for index, trace_id in enumerate(ids)
            ],
            span(trace_id=new_trace_id(), start=_at(30), trace={"session_id": "other"}),
        ],
    )
    url = _turns_url(workspace, "paged")
    traces_url = f"/api/v1/projects/{workspace.project_id}/traces"

    first = (await workspace.owner.get(url, params={"limit": 2})).json()
    listed = (
        await workspace.owner.get(traces_url, params={"session_id": "paged", "limit": 2})
    ).json()
    assert [item["trace"]["trace_id"] for item in first["items"]] == [ids[2], ids[1]]
    assert first["next_cursor"] == listed["next_cursor"] is not None

    rest = (
        await workspace.owner.get(url, params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [item["trace"]["trace_id"] for item in rest["items"]] == [ids[0]]
    assert rest["next_cursor"] is None
    assert (await workspace.owner.get(url, params={"limit": 201})).status_code == 422


async def test_session_turns_omits_payloads_over_budget(
    client: httpx.AsyncClient, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Three one-span turns of about 10 KB of payloads each; the page's budget holds two.
    monkeypatch.setattr(trace_queries, "TRACE_PAYLOAD_BUDGET_BYTES", 25_000)
    ids = [new_trace_id() for _ in range(3)]
    contexts = [f"context {index} " + "x" * 10_000 for index in range(3)]
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=trace_id,
                start=_at(index * 60),
                input=text,
                output=None,
                trace={"session_id": "big"},
            )
            for index, (trace_id, text) in enumerate(zip(ids, contexts, strict=True))
        ],
    )

    items = (await workspace.owner.get(_turns_url(workspace, "big"))).json()["items"]

    spans = [item["spans"][0] for item in items]
    assert [s["payload_omitted"] for s in spans] == [False, False, True]
    assert [s["input"] for s in spans] == [contexts[2], contexts[1], None]


async def test_session_turns_other_project_returns_empty(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    alice = await create_workspace(await browser_factory("alice@example.com"), org_name="Alice")
    bob = await create_workspace(await browser_factory("bob@example.com"), org_name="Bob")
    await _ingest(client, bob, [span(trace_id=new_trace_id(), trace={"session_id": "shared"})])

    own = await alice.owner.get(_turns_url(alice, "shared"))
    assert own.status_code == 200
    assert own.json() == {"items": [], "next_cursor": None}
    foreign = await alice.owner.get(f"/api/v1/projects/{bob.project_id}/sessions/shared/turns")
    assert foreign.status_code == 404
    assert len((await bob.owner.get(_turns_url(bob, "shared"))).json()["items"]) == 1


async def test_session_turns_environment_filter(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    prod, dev = new_trace_id(), new_trace_id()
    await _ingest(
        client,
        workspace,
        [
            span(trace_id=prod, start=_at(0), trace={"session_id": "mixed", "environment": "prod"}),
            span(trace_id=dev, start=_at(60), trace={"session_id": "mixed", "environment": "dev"}),
        ],
    )
    url = _turns_url(workspace, "mixed")

    only_dev = (await workspace.owner.get(url, params={"environment": "dev"})).json()
    assert [item["trace"]["trace_id"] for item in only_dev["items"]] == [dev]
    both = (await workspace.owner.get(url)).json()
    assert [item["trace"]["trace_id"] for item in both["items"]] == [dev, prod]
    none = (await workspace.owner.get(url, params={"environment": "staging"})).json()
    assert none == {"items": [], "next_cursor": None}


async def test_session_turns_path_ids_round_trip(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    # A slash travels percent-encoded; `.`, `..` and a leading `~` travel `~`-escaped, as on the
    # users routes (browsers resolve dot segments even when percent-encoded).
    ids = {session_id: new_trace_id() for session_id in ("a/b", "..", "~x")}
    await _ingest(
        client,
        workspace,
        [span(trace_id=trace_id, trace={"session_id": sid}) for sid, trace_id in ids.items()],
    )
    for wire_id, session_id in (("a%2Fb", "a/b"), ("~..", ".."), ("~~x", "~x")):
        response = await workspace.owner.get(_turns_url(workspace, wire_id))
        assert response.status_code == 200, response.text
        assert [item["trace"]["trace_id"] for item in response.json()["items"]] == [ids[session_id]]
