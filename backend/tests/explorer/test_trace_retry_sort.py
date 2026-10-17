"""The trace list sorted by retry cost, the `has_retries` filter, and the retry fields.

`sort=retry_cost_usd` orders known costs first, largest first, and unknown (null) costs last; ties
fall back to `started_at` then `trace_id`, both descending, and the three-part keyset cursor
pages through without repeats or gaps.
"""

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

NOW = datetime.now(UTC).replace(microsecond=0)
WINDOW = {"from": (NOW - timedelta(hours=4)).isoformat(), "to": NOW.isoformat()}
HASH = "c" * 32


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


def _attempts(trace_id: str, minutes: int, count: int, **overrides: Any) -> list[dict[str, Any]]:
    """`count` llm calls of one request in a trace that starts `minutes` before now."""
    start = NOW - timedelta(minutes=minutes)
    return [
        {
            **span(trace_id=trace_id, start=start + timedelta(seconds=index), **overrides),
            "request_hash": HASH,
        }
        for index in range(count)
    ]


@pytest.fixture
async def seeded(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """Traces with a large, a small (twice), a zero, an unknown and a never computed retry cost."""
    ids = {name: new_trace_id() for name in ("large", "small", "small_later", "zero", "cut", "old")}
    cut = {
        "usage": {"input_tokens": 1000, "output_tokens": None, "cached_tokens": 0},
        "attributes": {"usage.unreported_reason": "stream_error"},
    }
    spans = [
        *_attempts(ids["large"], 60, 3),
        *_attempts(ids["small"], 50, 2),
        *_attempts(ids["small_later"], 40, 2),
        *_attempts(ids["zero"], 30, 1),
        *_attempts(ids["cut"], 20, 2, **cut),
        *_attempts(ids["old"], 10, 2),
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            update(Trace)
            .where(Trace.trace_id == ids["old"])
            .values(retry_calls=None, retry_cost_usd=None)
        )
        await db.commit()
    return ids


def _url(workspace: Workspace) -> str:
    return f"/api/v1/projects/{workspace.project_id}/traces"


async def _list(workspace: Workspace, **params: Any) -> httpx.Response:
    return await workspace.owner.get(_url(workspace), params={**WINDOW, **params})


async def test_sort_by_retry_cost_puts_known_costs_first_and_unknown_last(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    response = await _list(workspace, sort="retry_cost_usd")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["trace_id"] for item in items] == [
        seeded[name] for name in ("large", "small_later", "small", "zero", "old", "cut")
    ]
    by_id = {item["trace_id"]: item for item in items}
    small = by_id[seeded["small"]]
    assert small["retry_calls"] == 1
    assert Decimal(by_id[seeded["large"]]["retry_cost_usd"]) == 2 * Decimal(small["retry_cost_usd"])
    zero = by_id[seeded["zero"]]
    assert (zero["retry_calls"], Decimal(zero["retry_cost_usd"])) == (0, 0)
    cut = by_id[seeded["cut"]]
    assert (cut["retry_calls"], cut["retry_cost_usd"]) == (1, None)


async def test_the_three_part_cursor_pages_without_repeats(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    whole = (await _list(workspace, sort="retry_cost_usd")).json()["items"]
    paged: list[str] = []
    cursor: str | None = None
    for _ in range(len(whole)):
        params: dict[str, Any] = {"sort": "retry_cost_usd", "limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        body = (await _list(workspace, **params)).json()
        paged += [item["trace_id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert paged == [item["trace_id"] for item in whole]


async def test_the_default_sort_is_unchanged(workspace: Workspace, seeded: dict[str, str]) -> None:
    first = (await _list(workspace, limit=3)).json()
    rest = (await _list(workspace, limit=3, cursor=first["next_cursor"])).json()

    ids = [item["trace_id"] for item in first["items"] + rest["items"]]
    assert ids == [seeded[name] for name in ("old", "cut", "zero", "small_later", "small", "large")]


async def test_a_cursor_of_the_other_sort_is_rejected(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    by_start = (await _list(workspace, limit=1)).json()["next_cursor"]

    response = await _list(workspace, sort="retry_cost_usd", cursor=by_start)

    assert response.status_code == 422, response.text


async def test_has_retries_keeps_only_traces_with_a_known_retry(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    with_retries = (await _list(workspace, has_retries="true")).json()["items"]
    without = (await _list(workspace, has_retries="false")).json()["items"]

    assert {item["trace_id"] for item in with_retries} == {
        seeded[name] for name in ("large", "small", "small_later", "cut")
    }
    assert [item["trace_id"] for item in without] == [seeded["zero"]]


async def test_an_unknown_sort_is_rejected(workspace: Workspace, seeded: dict[str, str]) -> None:
    response = await _list(workspace, sort="foo")

    assert response.status_code == 422, response.text


async def test_a_trace_not_recomputed_since_the_migration_reports_null(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    items = (await _list(workspace)).json()["items"]
    old = next(item for item in items if item["trace_id"] == seeded["old"])
    detail = (await workspace.owner.get(f"{_url(workspace)}/{seeded['old']}")).json()

    assert (old["retry_calls"], old["retry_cost_usd"]) == (None, None)
    assert (detail["retry_calls"], detail["retry_cost_usd"]) == (None, None)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"v": "NaN", "s": NOW.isoformat(), "t": "a"}, id="not-a-number"),
        pytest.param({"v": "1e999999", "s": NOW.isoformat(), "t": "a"}, id="huge-exponent"),
        pytest.param({"v": "1", "s": "2026-10-16T00:00:00", "t": "a"}, id="naive-time"),
        pytest.param({"v": 1, "s": NOW.isoformat(), "t": "a"}, id="number-not-string"),
        pytest.param({"v": "1", "s": NOW.isoformat(), "t": "a\x00"}, id="nul-tiebreaker"),
    ],
)
async def test_a_forged_retry_cost_cursor_is_rejected(
    workspace: Workspace, seeded: dict[str, str], payload: dict[str, Any]
) -> None:
    raw = json.dumps(payload).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    response = await _list(workspace, sort="retry_cost_usd", cursor=cursor)

    assert response.status_code == 422, response.text
