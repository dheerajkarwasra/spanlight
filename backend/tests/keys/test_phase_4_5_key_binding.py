"""A project API key reaches its own project and nothing else, on the Phase 4 and 5 routes.

Releases and end users (`/api/v1`, `traces:read`), prompt reads (`GET /v1/prompts/{name}`,
`prompts:read`) and score ingestion (`POST /v1/scores`, `scores:write`). With another project's
key the answer is the same 404 a project that does not exist gives, and nothing of the other
project is read or written; with the right project and a missing scope it is 403 `KEY_SCOPE`.
A gateway key (`spl_gw_`) belongs to `/gw/v1` only and a project key (`spl_live_`) to everything
but that (the Phase 2 ruling).
"""

from typing import Any

import httpx
import pytest

from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import Browser, bearer, create_api_key
from tests.keys.conftest import END_USER, RELEASES, World

PROMPT = "support/greeting"
PROMPT_BODY = {
    "name": PROMPT,
    "kind": "text",
    "template": {"kind": "text", "text": "Hello {{name}}"},
}
# The prompt has no `production` label; the highest version is the one to read.
LATEST = {"label": "latest"}
ALL_READ_SCOPES = ["traces:read", "prompts:read", "scores:write"]


class Keys:
    """Keys for both projects of the `world` fixture (project A has the prompt and the data)."""

    def __init__(self, world: World, a: str, b: str, b_no_traces: str, a_ingest: str) -> None:
        self.world = world
        self.a, self.b = a, b  # every read scope, for project A and for project B
        self.b_no_traces = b_no_traces  # project B, without `traces:read`
        self.a_ingest = a_ingest  # project A, `ingest:write` only

    @property
    def project_a(self) -> str:
        return self.world.workspace.project_id

    def project_path(self, route: str) -> str:
        return f"/api/v1/projects/{self.project_a}/{route}"


@pytest.fixture
async def keys(world: World, release_and_user_data: None) -> Keys:
    owner, project_a, project_b = world.owner, world.workspace.project_id, world.other_project_id
    a = await create_api_key(owner, project_a, scopes=ALL_READ_SCOPES)
    b = await create_api_key(owner, project_b, scopes=ALL_READ_SCOPES)
    b_no_traces = await create_api_key(owner, project_b, scopes=["prompts:read", "scores:write"])
    created = await owner.post(f"/api/v1/projects/{project_a}/prompts", json=PROMPT_BODY)
    assert created.status_code == 201, created.text
    return Keys(world, a["secret"], b["secret"], b_no_traces["secret"], world.workspace.api_key)


def code_of(response: httpx.Response) -> str | None:
    body = response.json() if response.content else None
    return body.get("code") if isinstance(body, dict) else None


def release_user_routes(keys: Keys) -> list[str]:
    """Releases and end users of project A, with values that exist in both projects."""
    return [
        keys.project_path("releases"),
        keys.project_path(f"releases/compare?a={RELEASES[0]}&b={RELEASES[1]}"),
        keys.project_path("users"),
        keys.project_path(f"users/{END_USER}"),
    ]


async def test_another_projects_key_on_releases_and_users_is_404(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    """Control first: project A's own key reads all four (and project B has the same data)."""
    for path in release_user_routes(keys):
        own = await bare_client.get(path, headers=bearer(keys.a))
        assert own.status_code == 200, (path, own.text)
        for foreign in (keys.b, keys.b_no_traces):
            refused = await bare_client.get(path, headers=bearer(foreign))
            assert (refused.status_code, code_of(refused)) == (404, "NOT_FOUND"), path


async def test_a_key_without_traces_read_on_its_own_project_is_key_scope(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    for path in release_user_routes(keys):
        refused = await bare_client.get(path, headers=bearer(keys.a_ingest))
        assert (refused.status_code, code_of(refused)) == (403, "KEY_SCOPE"), path


async def test_a_prompt_read_with_another_projects_key_is_404(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    """The key picks the project, so project B's key finds nothing under project A's names."""
    own = await bare_client.get(f"/v1/prompts/{PROMPT}", params=LATEST, headers=bearer(keys.a))
    assert own.status_code == 200, own.text
    assert own.json()["template"] == {"kind": "text", "text": "Hello {{name}}"}

    foreign = await bare_client.get(f"/v1/prompts/{PROMPT}", params=LATEST, headers=bearer(keys.b))

    assert (foreign.status_code, code_of(foreign)) == (404, "NOT_FOUND")
    assert "Hello" not in foreign.text


async def test_a_prompt_read_without_prompts_read_is_key_scope(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    for key in (keys.a_ingest, keys.world.read_key):
        refused = await bare_client.get(f"/v1/prompts/{PROMPT}", params=LATEST, headers=bearer(key))
        assert (refused.status_code, code_of(refused)) == (403, "KEY_SCOPE")
        assert "Hello" not in refused.text


async def scores_of_trace(owner: Browser, project_id: str, trace_id: str) -> list[dict[str, Any]]:
    response = await owner.get(f"/api/v1/projects/{project_id}/traces/{trace_id}/scores")
    assert response.status_code == 200, response.text
    scores: list[dict[str, Any]] = response.json()
    return scores


async def test_scores_sent_with_a_key_land_in_that_keys_project_only(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    """A score names a trace by id; the key, never the id, decides which project stores it."""
    world = keys.world
    trace_id = world.trace_id  # exists in project A only
    batch = {"scores": [{"trace_id": trace_id, "name": "quality", "value": 1}]}

    from_b = await bare_client.post("/v1/scores", json=batch, headers=bearer(keys.b))

    assert from_b.status_code == 200, from_b.text
    assert await scores_of_trace(world.owner, keys.project_a, trace_id) == []
    stored_in_b = await scores_of_trace(world.owner, world.other_project_id, trace_id)
    assert [score["name"] for score in stored_in_b] == ["quality"]

    from_a = await bare_client.post("/v1/scores", json=batch, headers=bearer(keys.a))

    assert from_a.status_code == 200, from_a.text
    in_a = await scores_of_trace(world.owner, keys.project_a, trace_id)
    assert [score["name"] for score in in_a] == ["quality"]
    assert {score["id"] for score in in_a}.isdisjoint({score["id"] for score in stored_in_b})


async def test_scores_without_scores_write_are_key_scope_and_store_nothing(
    bare_client: httpx.AsyncClient, keys: Keys
) -> None:
    trace_id = keys.world.trace_id
    batch = {"scores": [{"trace_id": trace_id, "name": "quality", "value": 1}]}
    reader = await create_api_key(keys.world.owner, keys.project_a, scopes=["traces:read"])

    for key in (keys.a_ingest, reader["secret"]):
        refused = await bare_client.post("/v1/scores", json=batch, headers=bearer(key))
        assert (refused.status_code, code_of(refused)) == (403, "KEY_SCOPE")

    assert await scores_of_trace(keys.world.owner, keys.project_a, trace_id) == []


@pytest.fixture
async def app(gateway_app: Any) -> Any:
    """The app that `browser_factory` signs people in to: one that can serve `/gw/v1`."""
    return gateway_app


API_V1_PATHS = [
    ("GET", "/api/v1/projects/{project}/traces"),
    ("GET", "/api/v1/projects/{project}/releases"),
    ("GET", "/api/v1/projects/{project}/users"),
    ("GET", "/api/v1/projects/{project}/prompts"),
    ("GET", "/api/v1/auth/me"),
]
KEY_PATHS = [
    ("POST", "/v1/traces", {"spans": []}),
    ("POST", "/v1/scores", {"scores": []}),
    ("GET", f"/v1/prompts/{PROMPT}", None),
]


async def test_a_gateway_key_is_unauthorized_on_the_dashboard_and_ingestion_apis(
    bare_client: httpx.AsyncClient, gateway_workspace: GatewayWorkspace
) -> None:
    headers = bearer(gateway_workspace.gateway_key)
    project = str(gateway_workspace.project_id)
    mismatches = []
    calls = [(m, p.format(project=project), None) for m, p in API_V1_PATHS] + KEY_PATHS
    for method, path, body in calls:
        response = await bare_client.request(method, path, json=body, headers=headers)
        if (response.status_code, code_of(response)) != (401, "UNAUTHORIZED"):
            mismatches.append(f"{method} {path}: {response.status_code} {code_of(response)}")
    assert mismatches == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/gw/v1/models", None),
        ("POST", "/gw/v1/chat/completions", {"model": "gpt-4o-mini", "messages": []}),
    ],
)
async def test_a_project_api_key_is_unauthorized_on_the_gateway(
    bare_client: httpx.AsyncClient,
    gateway_workspace: GatewayWorkspace,
    fake_provider: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    created = await create_api_key(
        gateway_workspace.owner, str(gateway_workspace.project_id), scopes=ALL_READ_SCOPES
    )
    response = await bare_client.request(method, path, json=body, headers=bearer(created["secret"]))
    assert response.status_code == 401, response.text
    assert fake_provider.requests == []  # refused before any provider call
