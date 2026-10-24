"""Usage of many prompt versions at once, for the versions list (UX Phase 5 ruling 11).

Failure modes: the grouped read disagrees with the single-version read the Usage card shows, an
unused version reads as "$0" instead of unknown, or another project's spans that carry the same
`prompt_version_id` value are counted.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.pagination import encode_cursor
from app.db.rls import bind_project
from app.prompts.usage import version_usage, versions_usage
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import ingest_rollup_pair


async def ingest(client: httpx.AsyncClient, api_key: str, *spans: dict[str, Any]) -> None:
    response = await client.post("/v1/traces", json={"spans": list(spans)}, headers=bearer(api_key))
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text


async def test_versions_usage_matches_single_version_usage(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    now = datetime.now(UTC)
    rolled, unpriced = uuid.uuid4(), uuid.uuid4()
    pair = await ingest_rollup_pair(
        browser_factory, client, now - timedelta(minutes=10), prompt_version_id=str(rolled)
    )
    trace_id = new_trace_id()
    await ingest(
        client,
        pair.workspace.api_key,
        span(trace_id=trace_id, prompt_version_id=str(unpriced)),
        span(trace_id=trace_id, prompt_version_id=str(unpriced), model="no-such-model-xyz"),
    )

    async with session_factory() as db, db.begin():
        await bind_project(db, pair.project_id)
        grouped = await versions_usage(db, pair.project_id, [rolled, unpriced], now)
        singles = {
            vid: await version_usage(db, pair.project_id, vid, now) for vid in (rolled, unpriced)
        }

    assert grouped == singles
    assert grouped[unpriced].has_unpriced is True and grouped[unpriced].llm_calls == 2


async def test_versions_usage_zero_fills_unused_version(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    project_id, unused = uuid.UUID(workspace.project_id), uuid.uuid4()

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        usage = (await versions_usage(db, project_id, [unused], datetime.now(UTC)))[unused]

    assert (usage.traces, usage.llm_calls) == (0, 0)
    assert (usage.cost_usd, usage.p95_ms, usage.has_unpriced) == (None, None, False)


async def test_versions_usage_ignores_other_project_spans(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    mine = await create_workspace(await browser_factory("owner@example.com"))
    theirs = await create_workspace(await browser_factory("other@example.com"), org_name="Other")
    shared = uuid.uuid4()
    for workspace in (mine, theirs, theirs):
        await ingest(
            client, workspace.api_key, span(trace_id=new_trace_id(), prompt_version_id=str(shared))
        )

    async with session_factory() as db, db.begin():
        await bind_project(db, uuid.UUID(mine.project_id))
        own = await versions_usage(db, uuid.UUID(mine.project_id), [shared], datetime.now(UTC))
        # Asking for the other project while bound to ours: row-level security hides its spans.
        foreign = await versions_usage(
            db, uuid.UUID(theirs.project_id), [shared], datetime.now(UTC)
        )

    assert own[shared].traces == 1
    assert foreign[shared].traces == 0


async def create_used_prompt(
    browser_factory: Any, client: httpx.AsyncClient
) -> tuple[Any, str, str]:
    """A prompt with two versions; one call used version 1. Returns (owner, prompts URL, v1 id)."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    prompts = f"/api/v1/projects/{workspace.project_id}/prompts"
    created = await workspace.owner.post(
        prompts, json={"name": "greet", "kind": "text", "template": {"kind": "text", "text": "Hi"}}
    )
    assert created.status_code == 201, created.text
    v1 = created.json()["latest"]["id"]
    added = await workspace.owner.post(
        f"{prompts}/greet/versions", json={"template": {"kind": "text", "text": "Hello"}}
    )
    assert added.status_code == 201, added.text
    await ingest(client, workspace.api_key, span(trace_id=new_trace_id(), prompt_version_id=v1))
    return workspace.owner, prompts, v1


async def test_list_versions_with_usage_returns_usage_7d(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    owner, prompts, v1 = await create_used_prompt(browser_factory, client)

    response = await owner.get(f"{prompts}/greet/versions", params={"usage": "true"})

    assert response.status_code == 200, response.text
    by_version = {item["version"]: item for item in response.json()["items"]}
    used = by_version[1]["usage_7d"]
    assert by_version[1]["id"] == v1
    assert (used["traces"], used["llm_calls"], used["has_unpriced"]) == (1, 1, False)
    assert isinstance(used["cost_usd"], str) and float(used["cost_usd"]) > 0
    assert by_version[2]["usage_7d"] == {
        "traces": 0,
        "llm_calls": 0,
        "cost_usd": None,
        "p95_ms": None,
        "has_unpriced": False,
    }


async def test_list_versions_without_usage_has_null_usage_7d(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    owner, prompts, _ = await create_used_prompt(browser_factory, client)

    response = await owner.get(f"{prompts}/greet/versions")

    assert response.status_code == 200, response.text
    assert [item["usage_7d"] for item in response.json()["items"]] == [None, None]


async def test_list_versions_with_usage_past_the_last_page_is_empty(
    browser_factory: Any, client: httpx.AsyncClient, monkeypatch: Any
) -> None:
    owner, prompts, _ = await create_used_prompt(browser_factory, client)
    reads: list[object] = []

    async def no_read(*args: Any) -> dict[uuid.UUID, Any]:
        reads.append(args)
        return {}

    # An empty page has nothing to read usage for: no grouped query with an empty IN list.
    monkeypatch.setattr("app.api.v1.prompts.versions_usage", no_read)
    past_end = encode_cursor(datetime.now(UTC), "1")

    response = await owner.get(
        f"{prompts}/greet/versions", params={"usage": "true", "cursor": past_end}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}
    assert reads == []
