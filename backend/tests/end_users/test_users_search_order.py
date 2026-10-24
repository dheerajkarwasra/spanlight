"""The Users list searches `external_user_id` (`q`) and sorts in either direction (`order`).

`q` is a case-insensitive substring match in which `%`, `_` and `\\` match themselves.
`order=asc` flips the value direction only: unknown values stay last and ties go by user id
ascending in both directions, so keyset pages cover every user once.
"""

from typing import Any

import httpx
import pytest

from app.end_users.jobs import refresh_all
from tests.end_users.test_refresh_lag import _anchor, _window
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory


async def _seed(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    users: dict[str, tuple[str, int]],
) -> tuple[Browser, Workspace, dict[str, str]]:
    """One trace per user with one LLM span of `(model, input tokens)`; refreshed stats."""
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    started = now.replace(hour=10)
    for user, (model, tokens) in users.items():
        body = span(
            trace_id=new_trace_id(),
            start=started,
            model=model,
            usage={"input_tokens": tokens, "output_tokens": 0, "cached_tokens": 0},
            trace={"name": "answer", "user_id": user},
        )
        response = await client.post(
            "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
        )
        assert response.status_code == 200 and response.json()["accepted"] == 1, response.text
    await refresh_all(session_factory, now=now)
    return owner, workspace, _window(today, today)


async def _list(
    owner: Browser, workspace: Workspace, window: dict[str, str], **params: str
) -> httpx.Response:
    path = f"/api/v1/projects/{workspace.project_id}/users"
    return await owner.get(path, params={**window, **params})


async def _ids(
    owner: Browser, workspace: Workspace, window: dict[str, str], **params: str
) -> list[str]:
    response = await _list(owner, workspace, window, **params)
    assert response.status_code == 200, response.text
    return [item["external_user_id"] for item in response.json()["items"]]


async def test_q_filters_case_insensitive_substring(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    users = {name: ("gpt-4o-mini", 1000) for name in ("Alice", "malice", "bob")}
    owner, workspace, window = await _seed(browser_factory, client, session_factory, users)

    assert sorted(await _ids(owner, workspace, window, q="ALIC")) == ["Alice", "malice"]
    assert await _ids(owner, workspace, window, q="bob") == ["bob"]
    assert await _ids(owner, workspace, window, q="nobody") == []
    assert len(await _ids(owner, workspace, window)) == 3


async def test_search_escapes_like_wildcards(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    names = ("a_b", "axb", "a%b", "a\\b", "ab")
    users = {name: ("gpt-4o-mini", 1000) for name in names}
    owner, workspace, window = await _seed(browser_factory, client, session_factory, users)

    assert await _ids(owner, workspace, window, q="_") == ["a_b"]
    assert await _ids(owner, workspace, window, q="%") == ["a%b"]
    assert await _ids(owner, workspace, window, q="\\") == ["a\\b"]
    assert await _ids(owner, workspace, window, q="a_") == ["a_b"]


async def test_q_and_order_are_validated(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace, window = await _seed(browser_factory, client, session_factory, {})

    for params in ({"q": ""}, {"q": "x" * 257}, {"q": "a\x00b"}, {"order": "up"}):
        response = await _list(owner, workspace, window, **params)
        assert response.status_code == 422, params


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        ("asc", ["u-a", "u-d", "u-b", "u-c", "u-n1", "u-n2"]),
        ("desc", ["u-c", "u-b", "u-a", "u-d", "u-n1", "u-n2"]),
    ],
)
async def test_cost_pages_cover_every_user_once(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    order: str,
    expected: list[str],
) -> None:
    users = {
        "u-n2": ("not-a-priced-model", 1000),
        "u-c": ("gpt-4o-mini", 3000),
        "u-a": ("gpt-4o-mini", 1000),
        "u-n1": ("not-a-priced-model", 1000),
        "u-b": ("gpt-4o-mini", 2000),
        "u-d": ("gpt-4o-mini", 1000),
    }
    owner, workspace, window = await _seed(browser_factory, client, session_factory, users)

    seen: list[str] = []
    costs: list[str | None] = []
    cursor: str | None = None
    for _ in range(10):
        params = {"sort": "cost", "order": order, "limit": "1"}
        if cursor:
            params["cursor"] = cursor
        response = await _list(owner, workspace, window, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item["external_user_id"] for item in body["items"]]
        costs += [item["cost_usd"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == expected
    assert costs[-2:] == [None, None] and None not in costs[:-2]
