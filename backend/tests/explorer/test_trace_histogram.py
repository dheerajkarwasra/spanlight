"""Trace counts over time for the trace list's filters.

Buckets are hours for a window of 48 h or less, else days, truncated in UTC; empty buckets are
left out; `errors` counts the traces with at least one failed span. The filters are the list's
own dependency, so the histogram counts exactly the traces the list pages through.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.explorer.score_filters import SCORE_FILTER_NEEDS_NAME
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

DAY_START = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
# Yesterday 10:00 UTC; every trace sits at a fixed offset from it, so buckets are predictable.
BASE = DAY_START - timedelta(days=1) + timedelta(hours=10)

# name: (offset from BASE, status, model, tag)
TRACES: dict[str, tuple[timedelta, str, str, str]] = {
    "a": (timedelta(minutes=5), "ok", "gpt-4o-mini", "beta"),
    "b": (timedelta(minutes=40), "error", "gpt-4o", "beta"),
    "c": (timedelta(hours=2, minutes=1), "ok", "gpt-4o", "prod"),
    "d": (timedelta(days=-3, hours=1), "error", "gpt-4o-mini", "beta"),
    "e": (timedelta(days=-3, hours=2), "ok", "gpt-4o-mini", "prod"),
}


def _url(workspace: Workspace, route: str) -> str:
    return f"/api/v1/projects/{workspace.project_id}/traces{route}"


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat()}


DAY_WINDOW = _window(BASE - timedelta(hours=1), BASE + timedelta(hours=23))
WEEK_WINDOW = _window(BASE - timedelta(days=6), BASE + timedelta(days=1))


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


@pytest.fixture
async def seeded(client: httpx.AsyncClient, workspace: Workspace) -> dict[str, str]:
    ids = {name: new_trace_id() for name in TRACES}
    spans = [
        span(
            trace_id=ids[name],
            start=BASE + offset,
            status=status,
            model=model,
            trace={"name": "answer", "environment": "prod", "tags": [tag]},
        )
        for name, (offset, status, model, tag) in TRACES.items()
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text
    return ids


async def _histogram(workspace: Workspace, **params: Any) -> dict[str, Any]:
    response = await workspace.owner.get(_url(workspace, "/histogram"), params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _bucket(start: datetime, traces: int, errors: int) -> dict[str, Any]:
    return {
        "bucket_start": start.isoformat().replace("+00:00", "Z"),
        "traces": traces,
        "errors": errors,
    }


async def test_hour_buckets_for_a_day_window(workspace: Workspace, seeded: dict[str, str]) -> None:
    body = await _histogram(workspace, **DAY_WINDOW)

    assert body == {
        "bucket": "hour",
        "buckets": [_bucket(BASE, 2, 1), _bucket(BASE + timedelta(hours=2), 1, 0)],
    }


async def test_day_buckets_for_a_week_window(workspace: Workspace, seeded: dict[str, str]) -> None:
    body = await _histogram(workspace, **WEEK_WINDOW)

    yesterday = DAY_START - timedelta(days=1)
    assert body == {
        "bucket": "day",
        "buckets": [_bucket(yesterday - timedelta(days=3), 2, 1), _bucket(yesterday, 3, 1)],
    }


async def test_empty_window_has_no_buckets(workspace: Workspace, seeded: dict[str, str]) -> None:
    body = await _histogram(
        workspace, **_window(BASE + timedelta(days=2), BASE + timedelta(days=3))
    )

    assert body == {"bucket": "hour", "buckets": []}


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"status": "error"},
        {"status": "ok"},
        {"model": "gpt-4o"},
        {"tag": "beta"},
        {"tag": "beta", "model": "gpt-4o-mini"},
        {"q": "answer"},
    ],
)
async def test_histogram_matches_list_filters(
    workspace: Workspace, seeded: dict[str, str], filters: dict[str, str]
) -> None:
    params = {**WEEK_WINDOW, **filters}
    body = await _histogram(workspace, **params)
    listed = await workspace.owner.get(_url(workspace, ""), params={**params, "limit": 200})
    items = listed.json()["items"]

    assert sum(bucket["traces"] for bucket in body["buckets"]) == len(items)
    assert sum(bucket["errors"] for bucket in body["buckets"]) == sum(
        1 for item in items if item["error_count"] > 0
    )


async def test_invalid_filter_is_refused_as_the_list_refuses_it(workspace: Workspace) -> None:
    response = await workspace.owner.get(_url(workspace, "/histogram"), params={"score_min": "1"})

    assert response.status_code == 422
    assert response.json()["code"] == SCORE_FILTER_NEEDS_NAME
