"""Metadata and name filters on the trace list, `metadata` on the summary, and the selector keys
and trace names `/filters` offers (spec §6.2, §6.3)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.helpers import Browser, Workspace, create_workspace, new_trace_id, span
from tests.ingest.support import ingest_native

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
WINDOW_START = NOW - timedelta(hours=4)
PAIR = {"a.b-c": "x"}


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


def _span(
    trace_id: str, *, name: str = "answer", metadata: dict[str, Any] | None = None, age_h: float = 1
) -> dict[str, Any]:
    trace: dict[str, Any] = {"name": name}
    if metadata is not None:
        trace["metadata"] = metadata
    return span(trace_id=trace_id, start=NOW - timedelta(hours=age_h), trace=trace)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await ingest_native(client, workspace, spans)
    assert response.json()["rejected"] == [], response.text


def _window() -> dict[str, str]:
    return {"from": WINDOW_START.isoformat(), "to": NOW.isoformat()}


async def _ids(workspace: Workspace, **params: str) -> list[str]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces", params={**_window(), **params}
    )
    assert response.status_code == 200, response.text
    return [item["trace_id"] for item in response.json()["items"]]


@pytest.fixture
async def seeded(client: httpx.AsyncClient, workspace: Workspace) -> dict[str, str]:
    """`acme` and `globex` carry a `customer_id`; `plain` carries no metadata at all."""
    ids = {name: new_trace_id() for name in ("acme", "globex", "plain")}
    await _ingest(
        client,
        workspace,
        [
            _span(ids["acme"], metadata={"customer_id": "acme", "plan": "free"}, age_h=1),
            _span(ids["globex"], metadata={"customer_id": "globex"}, age_h=2),
            _span(ids["plain"], name="answer-v2", age_h=3),
        ],
    )
    return ids


async def test_metadata_key_and_value_filter_by_containment(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    assert await _ids(workspace, metadata_key="customer_id", metadata_value="acme") == [
        seeded["acme"]
    ]
    # Exact value, not a prefix or substring.
    assert await _ids(workspace, metadata_key="customer_id", metadata_value="acm") == []
    assert await _ids(workspace, metadata_key="plan", metadata_value="free") == [seeded["acme"]]


async def test_metadata_key_alone_lists_traces_without_the_key(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    assert await _ids(workspace, metadata_key="customer_id") == [seeded["plain"]]
    assert await _ids(workspace, metadata_key="plan") == [seeded["globex"], seeded["plain"]]


async def test_metadata_value_alone_is_422(workspace: Workspace, seeded: dict[str, str]) -> None:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces",
        params={**_window(), "metadata_value": "acme"},
    )
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in problem["errors"]] == ["metadata_value"]


async def test_metadata_filter_binds_a_key_with_dot_and_dash(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    tagged, other = new_trace_id(), new_trace_id()
    await _ingest(
        client,
        workspace,
        [_span(tagged, metadata=PAIR), _span(other, metadata={"a.b-c": "y"}, age_h=2)],
    )

    assert await _ids(workspace, metadata_key="a.b-c", metadata_value="x") == [tagged]
    assert await _ids(workspace, metadata_key="a.b-c") == []
    # A key outside the pattern is refused by validation, never sent to the database.
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces",
        params={**_window(), "metadata_key": "A B"},
    )
    assert response.status_code == 422


async def test_metadata_filter_does_not_cross_projects(
    client: httpx.AsyncClient, browser_factory: Any
) -> None:
    """Two projects hold the same pair; each list shows only the caller's trace."""
    first = await create_workspace(await browser_factory("one@example.com"))
    second = await create_workspace(await browser_factory("two@example.com"))
    mine, theirs = new_trace_id(), new_trace_id()
    await _ingest(client, first, [_span(mine, metadata=PAIR)])
    await _ingest(client, second, [_span(theirs, metadata=PAIR)])

    assert await _ids(first, metadata_key="a.b-c", metadata_value="x") == [mine]
    assert await _ids(second, metadata_key="a.b-c", metadata_value="x") == [theirs]


async def test_name_filter_is_exact_not_substring(
    workspace: Workspace, seeded: dict[str, str]
) -> None:
    assert await _ids(workspace, name="answer-v2") == [seeded["plain"]]
    assert await _ids(workspace, name="answer") == [seeded["acme"], seeded["globex"]]
    assert await _ids(workspace, name="answ") == []


async def test_trace_summary_carries_metadata(workspace: Workspace, seeded: dict[str, str]) -> None:
    base = f"/api/v1/projects/{workspace.project_id}/traces"
    items = (await workspace.owner.get(base, params=_window())).json()["items"]
    by_id = {item["trace_id"]: item["metadata"] for item in items}
    assert by_id == {
        seeded["acme"]: {"customer_id": "acme", "plan": "free"},
        seeded["globex"]: {"customer_id": "globex"},
        seeded["plain"]: {},
    }
    detail = (await workspace.owner.get(f"{base}/{seeded['acme']}")).json()
    assert detail["metadata"] == {"customer_id": "acme", "plan": "free"}


async def test_filters_list_metadata_keys_and_trace_names_of_7_days_capped_at_100(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    # 101 distinct names; four traces carry 32 distinct keys each (128 keys), one is 8 days old.
    spans = [_span(new_trace_id(), name=f"name-{index:03d}") for index in range(101)]
    for group in range(4):
        keys = {f"k{group:02d}-{index:02d}": "v" for index in range(32)}
        spans.append(_span(new_trace_id(), name="grouped", metadata=keys))
    # The stale trace's key and name sort first, so the 7-day lookback is what keeps them out.
    spans.append(_span(new_trace_id(), name="a-stale", metadata={"a-stale": "v"}, age_h=8 * 24))
    await _ingest(client, workspace, spans)

    filters = (await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/filters")).json()
    keys, names = filters["metadata_keys"], filters["trace_names"]
    assert len(keys) == 100 and keys == sorted(keys) and keys[0] == "k00-00"
    assert len(names) == 100 and names == sorted(names) and names[0] == "grouped"
