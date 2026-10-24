"""`GET /sessions`: per-session fields, search, sorts in both directions, and the cost p99."""

import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Trace
from app.db.rls import bypass_rls
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
START = NOW - timedelta(hours=4)
WINDOW = {"from": START.isoformat(), "to": NOW.isoformat()}
SORTS = ["last", "cost", "turns", "errors", "tokens"]


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


def _turn(
    session_id: str, seconds: float, user_id: str | None = None, **overrides: Any
) -> dict[str, Any]:
    trace: dict[str, Any] = {"session_id": session_id}
    if user_id is not None:
        trace["user_id"] = user_id
    return span(
        trace_id=new_trace_id(),
        start=START + timedelta(seconds=seconds),
        trace=trace,
        **overrides,
    )


async def _list(workspace: Workspace, **params: Any) -> dict[str, Any]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/sessions", params={**WINDOW, **params}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_session_fields(client: httpx.AsyncClient, workspace: Workspace) -> None:
    solo = [
        _turn("solo", 0, "u1", duration_ms=100),
        _turn("solo", 60, "u1", duration_ms=200, status="error"),
        _turn(
            "solo",
            120,
            "u1",
            duration_ms=300,
            input={"messages": [{"role": "user", "content": "last question"}]},
        ),
    ]
    await _ingest(
        client,
        workspace,
        [*solo, _turn("pair", 10, "u1"), _turn("pair", 20, "u2"), _turn("anon", 30)],
    )

    by_id = {s["session_id"]: s for s in (await _list(workspace))["items"]}

    assert by_id["solo"] | {"cost_usd": None, "first_at": None, "last_at": None} == {
        "session_id": "solo",
        "trace_count": 3,
        "first_at": None,
        "last_at": None,
        "cost_usd": None,
        "has_unpriced": False,
        "error_count": 1,
        "external_user_id": "u1",
        "user_count": 1,
        "input_tokens": 3000,
        "output_tokens": 1500,
        "duration_ms": 120_300.0,
        "last_trace_id": solo[2]["trace_id"],
        "last_input_preview": None,
    }
    assert (by_id["pair"]["external_user_id"], by_id["pair"]["user_count"]) == (None, 2)
    assert (by_id["anon"]["external_user_id"], by_id["anon"]["user_count"]) == (None, 0)

    previewed = {s["session_id"]: s for s in (await _list(workspace, preview="true"))["items"]}
    assert previewed["solo"]["last_input_preview"] == "last question"
    assert previewed["anon"]["last_input_preview"] == "hi"


async def test_search_matches_session_prefix_or_exact_user(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await _ingest(
        client,
        workspace,
        [_turn("chat-1", 0, "alice"), _turn("chat-2", 10, "bob"), _turn("bob-x", 20, "carol")],
    )

    async def found(q: str) -> list[str]:
        return sorted(s["session_id"] for s in (await _list(workspace, q=q))["items"])

    assert await found("CHAT") == ["chat-1", "chat-2"]
    assert await found("chat-1") == ["chat-1"]
    assert await found("bob") == ["bob-x", "chat-2"]
    # The user id must match whole; the session id only from its start.
    assert await found("ali") == []
    assert await found("hat") == []
    # A matching session keeps every trace of the window, not only the user's.
    await _ingest(client, workspace, [_turn("chat-2", 30, "dave")])
    by_id = {s["session_id"]: s for s in (await _list(workspace, q="bob"))["items"]}
    assert (by_id["chat-2"]["trace_count"], by_id["chat-2"]["user_count"]) == (2, 2)


async def test_search_escapes_like_wildcards(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    sessions = ["a_%1", "ab%1", "a_b1", "a\\_%"]
    await _ingest(client, workspace, [_turn(sid, n) for n, sid in enumerate(sessions)])

    async def found(q: str) -> list[str]:
        return sorted(s["session_id"] for s in (await _list(workspace, q=q))["items"])

    assert await found("a_%") == ["a_%1"]
    assert await found("a\\") == ["a\\_%"]


async def test_search_rejects_over_256_chars(workspace: Workspace) -> None:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/sessions", params={**WINDOW, "q": "x" * 257}
    )
    assert response.status_code == 422


def _sort_value(session: dict[str, Any], sort: str) -> Any:
    if sort == "last":
        return session["last_at"]
    if sort == "cost":
        return None if session["cost_usd"] is None else Decimal(session["cost_usd"])
    if sort == "turns":
        return session["trace_count"]
    if sort == "errors":
        return session["error_count"]
    return session["input_tokens"] + session["output_tokens"]


@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize("sort", SORTS)
async def test_sort_pages_cover_every_session_once(
    client: httpx.AsyncClient, workspace: Workspace, sort: str, order: str
) -> None:
    unknown = {"model": "unknown-llm"}
    spans = [
        _turn("s1", 0),
        _turn("s1", 5, status="error"),
        _turn("s2", 10, **unknown),
        _turn("s3", 20),
        _turn("s4", 30, usage={"input_tokens": 9000, "output_tokens": 1, "cached_tokens": 0}),
        _turn("s4", 35, status="error"),
        _turn("s4", 40),
        _turn("s5", 50, **unknown),
        _turn("s6", 60),
        _turn("s7", 70, **unknown),
    ]
    await _ingest(client, workspace, spans)

    whole = (await _list(workspace, sort=sort, order=order, limit=200))["items"]
    ids, cursor, values = [], None, []
    while True:
        page = await _list(workspace, sort=sort, order=order, limit=2, cursor=cursor)
        ids += [s["session_id"] for s in page["items"]]
        if (cursor := page["next_cursor"]) is None:
            break
        values.append(json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))))

    assert ids == [s["session_id"] for s in whole]
    assert sorted(ids) == ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
    if sort == "cost":
        # Three unknown costs at two a page: one page starts among them, after a `null` cursor.
        assert any(value["v"] is None for value in values)
    # Known values in the sort's direction, unknowns last, ties by session id the same way.
    known = [s for s in whole if _sort_value(s, sort) is not None]
    keys = [(_sort_value(s, sort), s["session_id"]) for s in known]
    assert keys == sorted(keys, reverse=order == "desc")
    assert [s["session_id"] for s in whole[len(known) :]] == sorted(
        (s["session_id"] for s in whole if _sort_value(s, sort) is None),
        reverse=order == "desc",
    )


async def test_cursor_of_another_sort_is_rejected(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await _ingest(client, workspace, [_turn("s1", 0), _turn("s2", 10)])
    cursor = (await _list(workspace, limit=1))["next_cursor"]
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/sessions",
        params={**WINDOW, "limit": 1, "cursor": cursor, "sort": "cost"},
    )
    assert response.status_code == 422


async def test_cost_p99_needs_20_priced_sessions(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    def priced(n: int) -> dict[str, Any]:
        usage = {"input_tokens": 1000 * (n + 1), "output_tokens": 0, "cached_tokens": 0}
        return _turn(f"p{n:02}", n, usage=usage)

    # 19 priced sessions and one unpriced one: 20 sessions, but too few costs.
    await _ingest(
        client, workspace, [*(priced(n) for n in range(19)), _turn("x", 99, model="unknown-llm")]
    )
    assert (await _list(workspace, limit=1))["cost_p99_usd"] is None

    await _ingest(client, workspace, [priced(n) for n in range(19, 25)])
    body = await _list(workspace, limit=200)
    costs = sorted(Decimal(s["cost_usd"]) for s in body["items"] if s["cost_usd"] is not None)
    assert len(costs) == 25
    # percentile_cont: linear between the 24th and 25th of 25 values (rank 0.99 * 24).
    expected = costs[23] + (costs[24] - costs[23]) * Decimal("0.76")
    assert abs(Decimal(body["cost_p99_usd"]) - expected) <= Decimal("0.00000001")
    # The search does not narrow it; only the first page carries it.
    first = await _list(workspace, limit=1, q="p")
    assert first["cost_p99_usd"] == body["cost_p99_usd"]
    assert (await _list(workspace, limit=1, q="p", cursor=first["next_cursor"]))[
        "cost_p99_usd"
    ] is None


async def test_cost_p99_of_a_session_over_a_million_dollars(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Two traces at the per-trace column's top make one session cost $1.8M: the p99 must not
    # overflow a fixed precision and fail the whole first page.
    big = [_turn("big", 0), _turn("big", 1)]
    await _ingest(client, workspace, [*big, *(_turn(f"s{n:02}", n + 2) for n in range(19))])
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            update(Trace)
            .where(Trace.trace_id.in_([turn["trace_id"] for turn in big]))
            .values(cost_usd=Decimal("900000"))
        )
        await db.commit()

    body = await _list(workspace, limit=1)
    assert Decimal(body["cost_p99_usd"]) > Decimal("1000000")
