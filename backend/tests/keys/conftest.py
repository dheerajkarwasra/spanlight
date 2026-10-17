"""A project with a read key, shared by the tests that exercise API keys over HTTP."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.api import deps
from app.core.ratelimit import PostgresTokenBucket
from app.end_users.jobs import refresh_all
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_project,
    create_workspace,
    new_span_id,
    new_trace_id,
    span,
)

# The routes a `traces:read` key may call, for its own project only. `{trace}` is a trace id.
READ_ROUTES = [
    "/api/v1/projects/{project}/traces",
    "/api/v1/projects/{project}/traces/{trace}",
    "/api/v1/projects/{project}/sessions",
    "/api/v1/projects/{project}/filters",
    "/api/v1/projects/{project}/metrics/overview",
    "/api/v1/projects/{project}/metrics/timeseries",
    "/api/v1/projects/{project}/metrics/models",
]

TRACE_FIELDS = {"name": "answer", "environment": "prod", "session_id": "s1"}


@dataclass
class World:
    """One owner with two projects, each holding one trace, and a read key for the first."""

    owner: Browser
    workspace: Workspace  # the first project, with its default ingest key
    other_project_id: str
    read_key: str
    trace_id: str
    other_trace_id: str
    # The one span of each trace, by trace id, for routes that name a span.
    span_ids: dict[str, str]

    def path(
        self, template: str, *, project_id: str | None = None, trace: str | None = None
    ) -> str:
        return template.format(
            project=project_id or self.workspace.project_id, trace=trace or self.trace_id
        )


@pytest.fixture
async def world(browser_factory: Any, client: httpx.AsyncClient) -> World:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    other_project_id = await create_project(owner, workspace.org_id, "Other")
    other_ingest = await create_api_key(owner, other_project_id)
    read = await create_api_key(owner, workspace.project_id, scopes=["traces:read"])

    trace_id, other_trace_id = new_trace_id(), new_trace_id()
    span_ids = {trace_id: new_span_id(), other_trace_id: new_span_id()}
    for api_key, trace in ((workspace.api_key, trace_id), (other_ingest["secret"], other_trace_id)):
        body = span(trace_id=trace, span_id=span_ids[trace], trace=TRACE_FIELDS)
        response = await client.post("/v1/traces", json={"spans": [body]}, headers=bearer(api_key))
        assert response.status_code == 200, response.text
    return World(
        owner, workspace, other_project_id, read["secret"], trace_id, other_trace_id, span_ids
    )


@pytest.fixture
def unthrottled_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lift the per-credential read limit (bursts of 40) for tests that walk every route.

    The route inventories send one credential to every `/api/v1` route, far more reads than one
    burst allows; without this, later routes would answer 429 instead of what they decide.
    """
    monkeypatch.setattr(deps, "API_READ_LIMITER", PostgresTokenBucket(rate=1000, burst=10_000))


# The end user and the two releases `release_and_user_data` gives each project.
END_USER = "u1"
RELEASES = ("v1", "v2")


@pytest.fixture
async def release_and_user_data(
    world: World, client: httpx.AsyncClient, session_factory: Any
) -> None:
    """Give both projects of `world` two releases and an end user with refreshed statistics.

    The release comparison and the end-user detail answer 404 on a project without them, the
    same answer as for another tenant's project; with them, a credential that gets through gets
    a 200. Kept apart from `world`, whose own project must hold exactly one trace.
    """
    other_ingest = await create_api_key(world.owner, world.other_project_id)
    for api_key in (world.workspace.api_key, other_ingest["secret"]):
        for release in RELEASES:
            fields = {**TRACE_FIELDS, "release": release, "user_id": END_USER}
            response = await client.post(
                "/v1/traces",
                json={"spans": [span(trace_id=new_trace_id(), trace=fields)]},
                headers=bearer(api_key),
            )
            assert response.status_code == 200, response.text
    run = await refresh_all(session_factory, now=datetime.now(UTC))
    assert run.projects_failed == 0 and run.projects >= 2
