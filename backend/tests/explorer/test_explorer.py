"""Trace list/detail, sessions, filters and metrics endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_workspace,
    new_span_id,
    new_trace_id,
    span,
)

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
WINDOW_START = NOW - timedelta(hours=4)


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


def _llm(trace_id: str, start: datetime, duration_ms: float, **overrides: Any) -> dict[str, Any]:
    return span(trace_id=trace_id, start=start, duration_ms=duration_ms, **overrides)


@pytest.fixture
async def seeded(client: httpx.AsyncClient, workspace: Workspace) -> dict[str, str]:
    """Three traces in the current window, one in the previous window."""
    ids = {name: new_trace_id() for name in ("a", "b", "c", "old")}
    hour = timedelta(hours=1)
    await _ingest(
        client,
        workspace,
        [
            _llm(
                ids["a"],
                WINDOW_START + hour,
                100,
                trace={
                    "name": "answer",
                    "environment": "prod",
                    "release": "v1",
                    "session_id": "s1",
                    "user_id": "u1",
                    "tags": ["beta"],
                },
            ),
            _llm(
                ids["a"],
                WINDOW_START + hour + timedelta(seconds=1),
                300,
                model="claude-haiku-4-5",
                provider="anthropic",
            ),
            _llm(
                ids["b"],
                WINDOW_START + 2 * hour,
                200,
                status="error",
                trace={"name": "summarize", "environment": "dev", "session_id": "s1"},
            ),
            _llm(
                ids["c"],
                WINDOW_START + 3 * hour,
                400,
                model="unknown-llm",
                trace={"name": "answer", "environment": "prod", "session_id": "s2"},
            ),
            _llm(
                ids["old"],
                WINDOW_START - 2 * hour,
                1000,
                trace={"name": "answer", "environment": "prod"},
            ),
        ],
    )
    return ids


def _window(start: datetime = WINDOW_START, end: datetime = NOW) -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat()}


async def test_trace_list_filters_and_order(workspace: Workspace, seeded: dict[str, str]) -> None:
    base = f"/api/v1/projects/{workspace.project_id}/traces"
    owner = workspace.owner

    items = (await owner.get(base, params=_window())).json()["items"]
    assert [item["trace_id"] for item in items] == [seeded["c"], seeded["b"], seeded["a"]]
    first = next(item for item in items if item["trace_id"] == seeded["a"])
    assert first["models"] == ["claude-haiku-4-5", "gpt-4o-mini"]
    assert first["span_count"] == 2
    assert first["duration_ms"] == pytest.approx(1300)
    assert isinstance(first["cost_usd"], str)

    async def ids(**params: str) -> list[str]:
        response = await owner.get(base, params={**_window(), **params})
        return [item["trace_id"] for item in response.json()["items"]]

    assert await ids(environment="prod") == [seeded["c"], seeded["a"]]
    assert await ids(status="error") == [seeded["b"]]
    assert await ids(status="ok") == [seeded["c"], seeded["a"]]
    assert await ids(model="claude-haiku-4-5") == [seeded["a"]]
    assert await ids(session_id="s1") == [seeded["b"], seeded["a"]]
    assert await ids(user_id="u1") == [seeded["a"]]
    assert await ids(tag="beta") == [seeded["a"]]
    assert await ids(q="summ") == [seeded["b"]]
    assert await ids(q=seeded["c"]) == [seeded["c"]]
    assert await ids(q="100%_") == []


async def test_trace_summary_reports_first_error_message(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    failing, healthy = new_trace_id(), new_trace_id()
    start = WINDOW_START + timedelta(minutes=10)
    await _ingest(
        client,
        workspace,
        [
            _llm(failing, start, 50, trace={"name": "classify"}),
            _llm(
                failing,
                start + timedelta(seconds=2),
                50,
                status="error",
                status_message="RateLimitError: HTTP 429",
            ),
            _llm(
                failing,
                start + timedelta(seconds=1),
                50,
                status="error",
                status_message="TimeoutError: exceeded 30.0 s",
            ),
            _llm(healthy, start, 50),
        ],
    )

    items = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/traces", params=_window()
        )
    ).json()["items"]
    by_id = {item["trace_id"]: item for item in items}
    assert by_id[failing]["error_message"] == "TimeoutError: exceeded 30.0 s"
    assert by_id[healthy]["error_message"] is None

    detail = (
        await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{failing}")
    ).json()
    assert detail["error_message"] == "TimeoutError: exceeded 30.0 s"


async def test_trace_list_keyset_pagination(workspace: Workspace, seeded: dict[str, str]) -> None:
    base = f"/api/v1/projects/{workspace.project_id}/traces"
    first = (await workspace.owner.get(base, params={**_window(), "limit": 2})).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = (
        await workspace.owner.get(
            base, params={**_window(), "limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()
    assert [item["trace_id"] for item in second["items"]] == [seeded["a"]]
    assert second["next_cursor"] is None


async def test_trace_detail_includes_ordered_spans(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces/{seeded['a']}"
    )
    body = response.json()
    assert response.status_code == 200
    assert [s["model"] for s in body["spans"]] == ["gpt-4o-mini", "claude-haiku-4-5"]
    assert body["spans"][0]["duration_ms"] == pytest.approx(100)
    assert body["spans"][0]["input"] == {"messages": [{"role": "user", "content": "hi"}]}


async def test_time_window_validation(workspace: Workspace) -> None:
    base = f"/api/v1/projects/{workspace.project_id}/traces"
    backwards = await workspace.owner.get(base, params=_window(NOW, WINDOW_START))
    assert backwards.status_code == 422
    too_long = await workspace.owner.get(base, params=_window(NOW - timedelta(days=91), NOW))
    assert too_long.status_code == 422


async def test_sessions(workspace: Workspace, seeded: dict[str, str]) -> None:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/sessions", params=_window()
    )
    sessions = response.json()["items"]
    assert [s["session_id"] for s in sessions] == ["s2", "s1"]
    s1 = sessions[1]
    assert s1["trace_count"] == 2 and s1["error_count"] == 1

    page = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/sessions", params={**_window(), "limit": 1}
        )
    ).json()
    rest = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/sessions",
            params={**_window(), "limit": 1, "cursor": page["next_cursor"]},
        )
    ).json()
    assert [s["session_id"] for s in page["items"] + rest["items"]] == ["s2", "s1"]


async def test_filters(workspace: Workspace, seeded: dict[str, str]) -> None:
    filters = (await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/filters")).json()
    assert filters == {
        "environments": ["dev", "prod"],
        "releases": ["v1"],
        "models": ["claude-haiku-4-5", "gpt-4o-mini", "unknown-llm"],
        "tags": ["beta"],
        # Phase 5: names of scores on the project's traces; this seed has none.
        "score_names": [],
    }


async def test_overview_with_previous_period(workspace: Workspace, seeded: dict[str, str]) -> None:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/metrics/overview", params=_window()
    )
    body = response.json()
    current = body["current"]
    assert current["traces"] == 3
    assert current["llm_calls"] == 4
    assert current["error_rate"] == pytest.approx(0.25)
    assert current["unpriced_calls"] == 1
    assert current["input_tokens"] == 4000 and current["output_tokens"] == 2000
    # Durations 100, 200, 300, 400: continuous percentiles interpolate.
    assert current["p50_ms"] == pytest.approx(250)
    assert current["p95_ms"] == pytest.approx(385)
    assert isinstance(current["cost_usd"], str)

    previous = body["previous"]
    assert previous["traces"] == 1 and previous["llm_calls"] == 1
    assert previous["p50_ms"] == pytest.approx(1000)

    prod = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/metrics/overview",
        params={**_window(), "environment": "prod"},
    )
    assert prod.json()["current"]["traces"] == 2


async def test_overview_empty_window_has_nulls_not_zeros(workspace: Workspace) -> None:
    current = (
        await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/metrics/overview")
    ).json()["current"]
    assert current["llm_calls"] == 0
    assert current["error_rate"] is None
    assert current["p95_ms"] is None
    assert current["cost_usd"] is None


async def test_timeseries_has_gapless_buckets(workspace: Workspace, seeded: dict[str, str]) -> None:
    points = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/metrics/timeseries",
            params={**_window(), "bucket": "hour"},
        )
    ).json()
    assert len(points) == 4
    assert [p["llm_calls"] for p in points] == [0, 2, 1, 1]
    assert [p["errors"] for p in points] == [0, 0, 1, 0]
    assert points[0]["cost_usd"] is None
    assert points[1]["tokens"] == 3000

    daily = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/metrics/timeseries",
        params={**_window(), "bucket": "day"},
    )
    assert sum(p["llm_calls"] for p in daily.json()) == 4


async def test_models_breakdown(workspace: Workspace, seeded: dict[str, str]) -> None:
    rows = (
        await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/metrics/models", params=_window()
        )
    ).json()
    by_model = {row["model"]: row for row in rows}
    assert rows[0]["model"] == "gpt-4o-mini"
    assert by_model["gpt-4o-mini"]["calls"] == 2
    assert by_model["gpt-4o-mini"]["errors"] == 1
    assert by_model["unknown-llm"]["cost_usd"] is None
    assert by_model["claude-haiku-4-5"]["provider"] == "anthropic"


async def test_onboarding_flips_after_first_trace(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    url = f"/api/v1/projects/{workspace.project_id}/onboarding"
    assert (await workspace.owner.get(url)).json() == {"has_traces": False, "first_trace_at": None}
    await _ingest(client, workspace, [span(trace_id=new_trace_id(), span_id=new_span_id())])
    status = (await workspace.owner.get(url)).json()
    assert status["has_traces"] is True and status["first_trace_at"]
