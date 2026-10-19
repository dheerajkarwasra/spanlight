"""Content search never crosses projects: through the API and with row-level security alone."""

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from app.db.rls import bind_project
from app.explorer.content_search import SEARCH_TEXT
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NEEDLE = "quokka-7731"


async def _ingest_needle(client: httpx.AsyncClient, workspace: Workspace) -> str:
    trace_id = new_trace_id()
    response = await client.post(
        "/v1/traces",
        json={
            "spans": [
                span(
                    trace_id=trace_id,
                    output={"role": "assistant", "content": f"The courier {NEEDLE} has it."},
                )
            ]
        },
        headers=bearer(workspace.api_key),
    )
    assert response.json()["accepted"] == 1, response.text
    return trace_id


async def test_content_search_stays_in_its_project(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice: Browser = await browser_factory("alice@example.com")
    bob: Browser = await browser_factory("bob@example.com")
    alice_ws = await create_workspace(alice, org_name="Alice Co")
    bob_ws = await create_workspace(bob, org_name="Bob Co")
    alice_trace = await _ingest_needle(client, alice_ws)
    bob_trace = await _ingest_needle(client, bob_ws)

    for workspace, own in ((alice_ws, alice_trace), (bob_ws, bob_trace)):
        response = await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/traces", params={"q": NEEDLE.upper()}
        )
        assert response.status_code == 200, response.text
        assert [item["trace_id"] for item in response.json()["items"]] == [own]

    # The policy alone, with no project condition in the query, hides Bob's matching span.
    async with session_factory() as db:
        await bind_project(db, alice_ws.project_id)
        found = (
            await db.scalars(select(Span.trace_id).where(SEARCH_TEXT.ilike(f"%{NEEDLE}%")))
        ).all()
    assert found == [alice_trace]
