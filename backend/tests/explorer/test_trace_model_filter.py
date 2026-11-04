"""The trace list's model filter keeps exactly the window's traces with a span of that model.

The filter reads spans only from the window's start on (a trace starts at its earliest span).
Oracle: the unfiltered list in the same sort, status and window, kept to the traces the test
stored a span of the model for. Generated traces cover several models per trace, none at all,
spans after the window's end, a trace that a late span moves out of the window, equal start
times, unknown cost and tokens, and a second project that stores the same trace ids.
"""

import random
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import httpx
import pytest

from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NOW = datetime.now(UTC).replace(second=0, microsecond=0)
WINDOW = {
    "from": (NOW - timedelta(hours=8)).isoformat(),
    "to": (NOW - timedelta(hours=3)).isoformat(),
}
MODELS = ("m-1", "m-2")


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


def _span(rng: random.Random, trace_id: str, start: datetime, model: str | None) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "status": "error" if rng.random() < 0.25 else "ok",
        "model": model,
        "provider": "openai" if model else None,
        "kind": "llm" if model else "tool",
    }
    if model is None or rng.random() < 0.2:
        overrides["usage"] = None
    else:
        overrides["usage"] = {
            "input_tokens": rng.randrange(1, 5000),
            "output_tokens": rng.randrange(1, 900),
            "cached_tokens": 0,
        }
    return span(trace_id=trace_id, start=start, duration_ms=rng.uniform(5, 90_000), **overrides)


def _generate(rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Spans of 60 traces, a late batch, and the id of the trace the late batch moves out."""
    spans: list[dict[str, Any]] = []
    tie = NOW - timedelta(hours=5)
    for index in range(60):
        trace_id = new_trace_id()
        # From an hour before the window to its end; two traces share a start time.
        start = tie if index < 2 else NOW - timedelta(minutes=rng.uniform(180, 540))
        for offset in range(rng.randint(1, 4)):
            # Later spans can start after the window's end (up to 90 minutes in).
            at = start + timedelta(minutes=offset * rng.uniform(0, 45))
            model = rng.choice((*MODELS, None))
            spans.append(_span(rng, trace_id, at, model))
    moved = new_trace_id()
    spans.append(_span(rng, moved, NOW - timedelta(hours=7, minutes=50), "m-1"))
    late = [_span(rng, moved, NOW - timedelta(hours=8, minutes=30), None)]
    return spans, late, moved


async def _all_pages(
    client: Browser, workspace: Workspace, params: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        page_params = {**WINDOW, **params, "limit": limit}
        if cursor:
            page_params["cursor"] = cursor
        response = await client.get(
            f"/api/v1/projects/{workspace.project_id}/traces", params=page_params
        )
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


@pytest.fixture
async def workspaces(browser_factory: Any) -> tuple[Workspace, Workspace]:
    return (
        await create_workspace(await browser_factory("owner@example.com")),
        await create_workspace(await browser_factory("other@example.com"), org_name="Other"),
    )


async def test_model_filter_equals_the_unfiltered_list_kept_to_the_model(
    client: httpx.AsyncClient, workspaces: tuple[Workspace, Workspace]
) -> None:
    workspace, other = workspaces
    rng = random.Random(1038)  # noqa: S311 - reproducible test data, not a secret
    spans, late, moved = _generate(rng)
    await _ingest(client, workspace, spans)
    await _ingest(client, workspace, late)
    # The other project stores the same trace ids, every span of each model, in the window.
    await _ingest(
        client,
        other,
        [
            {**body, "span_id": f"{index:016x}", "model": model, "kind": "llm"}
            for index, (body, model) in enumerate(product(spans, MODELS), start=1)
        ],
    )
    with_model = {
        model: {body["trace_id"] for body in [*spans, *late] if body["model"] == model}
        for model in MODELS
    }
    with_model["absent"] = set()
    owner = workspace.owner

    checked = 0
    for model, traces in with_model.items():
        for status in (None, "error"):
            for sort in ("started_at", "cost", "duration", "tokens"):
                for order in ("desc", "asc"):
                    params = {"sort": sort, "order": order}
                    if status:
                        params["status"] = status
                    unfiltered = await _all_pages(owner, workspace, params, 200)
                    expected = [item for item in unfiltered if item["trace_id"] in traces]
                    filtered = await _all_pages(owner, workspace, {**params, "model": model}, 4)
                    assert filtered == expected, (model, status, sort, order)
                    assert moved not in {item["trace_id"] for item in filtered}
                    checked += len(filtered)
    assert checked > 0
    # The window holds traces of each model, so the comparison is not of two empty lists.
    for model in MODELS:
        first = await _all_pages(owner, workspace, {"model": model}, 200)
        assert len(first) >= 10, model
        assert all(model in item["models"] for item in first)
