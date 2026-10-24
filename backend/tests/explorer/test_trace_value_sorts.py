"""The trace list sorted by duration, cost or tokens, in either direction.

Unknown (null) values come last in both directions; ties fall back to `started_at` then
`trace_id` in the sort's direction, and the three-part keyset cursor pages through without
repeats or gaps, also across the boundary between known and unknown values.
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

# name: (minutes before now, duration ms, cost, input tokens, output tokens), oldest first.
TRACES: dict[str, tuple[int, float, str | None, int, int]] = {
    "a": (70, 250.5, "0.50", 100, 50),
    "b": (60, 1200.0, "0.10", 3000, 0),
    "c": (50, 80.25, "0.10", 10, 5),
    "d": (40, 4000.0, None, 700, 300),
    "e": (30, 15.0, "2.00", 0, 0),
    "f": (20, 640.0, None, 50, 25),
    "g": (10, 3000.0, "0.00", 1500, 1500),
    "h": (5, 640.0, "0.30", 200, 100),
}

# Ascending order of each sort, unknown values last; equal values in start order. With two per
# page, the equal durations ("f", "h") and costs ("b", "c") fall on either side of a page break.
ASCENDING = {
    "duration": ["e", "c", "a", "f", "h", "b", "g", "d"],
    "cost": ["g", "b", "c", "h", "a", "e", "d", "f"],
    "tokens": ["e", "c", "f", "a", "h", "d", "b", "g"],
}


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


@pytest.fixture
async def seeded(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    ids = {name: new_trace_id() for name in TRACES}
    spans = [
        span(trace_id=ids[name], start=NOW - timedelta(minutes=minutes), duration_ms=duration)
        for name, (minutes, duration, *_) in TRACES.items()
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text
    async with session_factory() as db:
        await bypass_rls(db)
        for name, (_, _, cost, input_tokens, output_tokens) in TRACES.items():
            await db.execute(
                update(Trace)
                .where(Trace.trace_id == ids[name])
                .values(
                    cost_usd=None if cost is None else Decimal(cost),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
        await db.commit()
    return ids


async def _list(workspace: Workspace, **params: Any) -> httpx.Response:
    url = f"/api/v1/projects/{workspace.project_id}/traces"
    return await workspace.owner.get(url, params={**WINDOW, **params})


async def _paged(workspace: Workspace, **params: Any) -> list[str]:
    """Every trace id, two per page, following `next_cursor` to the end."""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(len(TRACES)):
        page_params = {**params, "limit": 2, **({"cursor": cursor} if cursor else {})}
        response = await _list(workspace, **page_params)
        assert response.status_code == 200, response.text
        body = response.json()
        ids += [item["trace_id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("the pages did not end")


def _expected(sort: str, order: str) -> list[str]:
    ascending = ASCENDING[sort]
    if order == "asc":
        return ascending
    known = [name for name in ascending if sort != "cost" or TRACES[name][2] is not None]
    unknown = [name for name in ascending if name not in known]
    # Descending flips the known values and the tiebreak; unknown values stay last.
    return known[::-1] + unknown[::-1]


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", ["duration", "cost", "tokens"])
async def test_value_sort_order(
    workspace: Workspace, seeded: dict[str, str], sort: str, order: str
) -> None:
    paged = await _paged(workspace, sort=sort, order=order)

    assert paged == [seeded[name] for name in _expected(sort, order)]


@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_cost_sort_pages_cover_every_trace_once(
    workspace: Workspace, seeded: dict[str, str], order: str
) -> None:
    paged = await _paged(workspace, sort="cost", order=order)

    assert sorted(paged) == sorted(seeded.values())
    assert len(paged) == len(set(paged))


async def test_started_at_asc_is_oldest_first(workspace: Workspace, seeded: dict[str, str]) -> None:
    oldest_first = await _paged(workspace, order="asc")
    newest_first = await _paged(workspace)

    assert oldest_first == [seeded[name] for name in TRACES]
    assert newest_first == oldest_first[::-1]


@pytest.mark.parametrize("params", [{"sort": "bogus"}, {"order": "sideways"}])
async def test_unknown_sort_is_422(
    workspace: Workspace, seeded: dict[str, str], params: dict[str, str]
) -> None:
    response = await _list(workspace, **params)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("value", ["1e39", "-1e39"])
async def test_a_duration_cursor_past_any_duration_is_not_an_error(
    workspace: Workspace, seeded: dict[str, str], value: str
) -> None:
    raw = json.dumps({"v": value, "s": NOW.isoformat(), "t": "f" * 32}).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    response = await _list(workspace, sort="duration", order="desc", cursor=cursor)

    assert response.status_code == 200, response.text
    expected = len(TRACES) if value == "1e39" else 0
    assert len(response.json()["items"]) == expected
