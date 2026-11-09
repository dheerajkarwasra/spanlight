"""The Users list merges the traces after each day's last recompute (the raw tail).

Failure mode: the list read only the per-user daily stats the worker recomputes every 15 minutes,
so a new project's users read "Not computed yet" and a busy one lagged by up to a pass. The tail,
capped at 24 hours (`RAW_TAIL_MAX`), is now summed from the raw traces in the same statement; only
what lies beyond the cap stays in `pending_traces`.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.end_users.jobs import refresh_all
from tests.end_users.test_refresh_lag import _window
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, execute, fetch

UNPRICED = "no-such-model-xyz"


async def _ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    user: str,
    start: datetime,
    model: str = "gpt-4o-mini",
    input_tokens: int = 1000,
) -> None:
    body = span(
        trace_id=new_trace_id(),
        start=start,
        duration_ms=1.0,
        model=model,
        usage={"input_tokens": input_tokens, "output_tokens": 0, "cached_tokens": 0},
        trace={"name": "answer", "user_id": user},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1, response.text


async def _refresh(session_factory: SessionFactory, project_id: str) -> datetime:
    """Recompute the last three days and return the recompute time, once it has passed."""
    await refresh_all(session_factory, now=datetime.now(UTC))
    rows = await fetch(
        session_factory,
        "SELECT max(computed_at) AS at FROM user_stats_daily WHERE project_id = :p",
        {"p": uuid.UUID(project_id)},
    )
    await asyncio.sleep(0.05)
    at: datetime = rows[0]["at"]
    return at


async def _list(owner: Browser, workspace: Workspace, **params: Any) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    response = await owner.get(
        f"/api/v1/projects/{workspace.project_id}/users",
        params={**_window(today - timedelta(days=2), today), **params},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _setup(browser_factory: Any) -> tuple[Browser, Workspace]:
    owner: Browser = await browser_factory("owner@example.com")
    return owner, await create_workspace(owner)


async def test_merged_totals_equal_recompute(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace = await _setup(browser_factory)
    early = datetime.now(UTC) - timedelta(minutes=10)
    await _ingest(client, workspace, "both", early, input_tokens=2000)
    await _ingest(client, workspace, "computed", early)
    computed_at = await _refresh(session_factory, workspace.project_id)

    after = computed_at + timedelta(milliseconds=1)
    await _ingest(client, workspace, "both", after, input_tokens=500)
    await _ingest(client, workspace, "both", after, model=UNPRICED)
    await _ingest(client, workspace, "tail", after, input_tokens=3000)

    merged = await _list(owner, workspace)
    assert merged["live"] is True and merged["pending_traces"] == 0
    assert {item["external_user_id"] for item in merged["items"]} == {"both", "computed", "tail"}

    await _refresh(session_factory, workspace.project_id)
    recomputed = await _list(owner, workspace)
    assert merged["items"] == recomputed["items"]


async def test_tail_unpriced_call_makes_cost_null(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace = await _setup(browser_factory)
    await _ingest(client, workspace, "free", datetime.now(UTC) - timedelta(minutes=5), UNPRICED)

    body = await _list(owner, workspace)
    (item,) = body["items"]
    assert item["external_user_id"] == "free"
    assert (item["cost_usd"], item["unpriced_calls"], item["llm_calls"]) == (None, 1, 1)
    assert body["computed_at"] is None and body["live"] is True


async def test_keyset_pagination_over_merged_rows(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace = await _setup(browser_factory)
    early = datetime.now(UTC) - timedelta(minutes=10)
    for user, tokens in (("a", 1000), ("b", 3000), ("c", 1000)):
        await _ingest(client, workspace, user, early, input_tokens=tokens)
    after = await _refresh(session_factory, workspace.project_id) + timedelta(milliseconds=1)
    await _ingest(client, workspace, "a", after, input_tokens=4000)
    await _ingest(client, workspace, "d", after, input_tokens=2000)
    await _ingest(client, workspace, "e", after, model=UNPRICED)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = await _list(owner, workspace, **params)
        seen += [item["external_user_id"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert cursor is None
    # By cost: a (1000 + 4000 tokens), b (3000), d (2000), c = 1000 by user id, e unknown last.
    assert seen == ["a", "b", "d", "c", "e"]


async def test_tail_capped_at_24h(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace = await _setup(browser_factory)
    now = datetime.now(UTC)
    await _ingest(client, workspace, "old", now - timedelta(hours=30))
    await _ingest(client, workspace, "new", now - timedelta(hours=1))

    body = await _list(owner, workspace)
    assert [item["external_user_id"] for item in body["items"]] == ["new"]
    assert body["pending_traces"] == 1 and body["live"] is False


async def test_live_flag_true_when_tail_merged(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace = await _setup(browser_factory)
    await _ingest(client, workspace, "u", datetime.now(UTC) - timedelta(minutes=5))
    body = await _list(owner, workspace)
    assert body["live"] is True and [i["external_user_id"] for i in body["items"]] == ["u"]

    # Coverage unavailable: no raw tail, as before it existed.
    await execute(
        session_factory,
        "UPDATE projects SET aggregate_history_unverified_before = now() + interval '1 day' "
        "WHERE id = :p",
        {"p": uuid.UUID(workspace.project_id)},
    )
    body = await _list(owner, workspace)
    assert body["items"] == [] and body["live"] is False and body["pending_traces"] is None


async def test_trace_ahead_of_the_recompute_counts_once(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    """Ingest accepts a start a few minutes ahead (clock skew). The recompute stored such a trace
    and the tail, which owns everything after the recompute, read it again once its start had
    passed: counted twice."""
    owner, workspace = await _setup(browser_factory)
    await _ingest(client, workspace, "ahead", datetime.now(UTC) - timedelta(minutes=5))
    await _ingest(client, workspace, "ahead", datetime.now(UTC) + timedelta(seconds=2))
    await _refresh(session_factory, workspace.project_id)
    await asyncio.sleep(2.5)  # the list window ends at the request time

    (item,) = (await _list(owner, workspace))["items"]
    assert (item["traces"], item["llm_calls"]) == (2, 2)


async def test_user_without_llm_calls_reads_zero_tokens(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    """Tokens are unknown only when calls were made and none reported usage; a user with no LLM
    call read "—" in the list and 0 in the detail."""
    owner, workspace = await _setup(browser_factory)
    body = span(
        trace_id=new_trace_id(),
        kind="tool",
        name="search",
        model=None,
        provider=None,
        usage=None,
        trace={"name": "lookup", "user_id": "tools-only"},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1, response.text
    await _refresh(session_factory, workspace.project_id)

    (item,) = (await _list(owner, workspace))["items"]
    assert (item["llm_calls"], item["tokens"]) == (0, 0)


async def test_detail_opens_for_a_user_only_the_tail_knows(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    """The list showed a user from the raw tail and the link to their page answered 404 until the
    next recompute; a user with stored days read lower totals there than in the list."""
    owner, workspace = await _setup(browser_factory)
    early = datetime.now(UTC) - timedelta(minutes=10)
    await _ingest(client, workspace, "both", early, input_tokens=2000)
    computed_at = await _refresh(session_factory, workspace.project_id)
    after = computed_at + timedelta(milliseconds=1)
    await _ingest(client, workspace, "both", after, input_tokens=500)
    await _ingest(client, workspace, "tail", after, input_tokens=3000)

    listed = {item["external_user_id"]: item for item in (await _list(owner, workspace))["items"]}
    today = datetime.now(UTC).date()
    for user in ("tail", "both"):
        response = await owner.get(
            f"/api/v1/projects/{workspace.project_id}/users/{user}",
            params=_window(today - timedelta(days=2), today),
        )
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["user"] == listed[user]
        assert detail["live"] is True
        assert sum(day["traces"] for day in detail["daily"]) == listed[user]["traces"]


async def test_detail_keeps_unknown_cost_beside_a_day_without_calls(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    """A tool-only stored day reads cost 0. With every call of the tail unpriced the total is
    unknown, as on the list; the detail read 0."""
    owner, workspace = await _setup(browser_factory)
    now = datetime.now(UTC)
    tools = span(
        trace_id=new_trace_id(),
        start=now - timedelta(days=1, hours=1),
        kind="tool",
        name="search",
        model=None,
        provider=None,
        usage=None,
        trace={"name": "lookup", "user_id": "mixed"},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [tools]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200, response.text
    await _refresh(session_factory, workspace.project_id)
    await _ingest(client, workspace, "mixed", datetime.now(UTC), model=UNPRICED)

    (listed,) = (await _list(owner, workspace))["items"]
    assert listed["cost_usd"] is None
    today = now.date()
    response = await owner.get(
        f"/api/v1/projects/{workspace.project_id}/users/mixed",
        params=_window(today - timedelta(days=2), today),
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"] == listed
