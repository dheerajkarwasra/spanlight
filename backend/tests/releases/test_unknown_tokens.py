"""A release keeps its token totals unknown when none of its calls reported usage.

Failure mode: a release whose only call failed before the provider answered read "0 tokens" in
the release comparison, and its token deltas read as a real change, where the project rule is
that an unknown value is NULL, never 0.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.releases.service import list_releases
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory


async def test_release_without_reported_usage_has_unknown_tokens(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    start = datetime.now(UTC) - timedelta(minutes=10)
    spans = [
        span(trace_id=new_trace_id(), start=start, usage=usage, trace={"release": release})
        for release, usage in (
            ("v-failed", None),
            ("v-ok", {"input_tokens": 30, "output_tokens": 7}),
        )
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text
    project_id = uuid.UUID(workspace.project_id)

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        window = TimeWindow(start - timedelta(minutes=1), start + timedelta(minutes=1))
        releases = {r.release: r for r in await list_releases(db, project_id, window, None)}

    assert (releases["v-failed"].input_tokens, releases["v-failed"].output_tokens) == (None, None)
    assert (releases["v-ok"].input_tokens, releases["v-ok"].output_tokens) == (30, 7)
