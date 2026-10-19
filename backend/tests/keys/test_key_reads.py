"""A `traces:read` key reads its own project, and nothing else; bearer requests ignore cookies."""

import uuid
from typing import Any

import httpx
import pytest

from tests.helpers import bearer
from tests.keys.conftest import READ_ROUTES, World


@pytest.mark.parametrize("template", READ_ROUTES)
async def test_a_read_key_reads_its_own_project(
    client: httpx.AsyncClient, world: World, template: str
) -> None:
    response = await client.get(world.path(template), headers=bearer(world.read_key))

    assert response.status_code == 200, response.text


async def test_traces_read_key_lists_own_project_traces(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(
        f"/api/v1/projects/{world.workspace.project_id}/traces", headers=bearer(world.read_key)
    )

    assert response.status_code == 200
    # Row-level security is bound to the key's project, so the other project's trace is absent.
    assert [item["trace_id"] for item in response.json()["items"]] == [world.trace_id]


async def test_a_read_key_gets_trace_detail_and_metrics(
    client: httpx.AsyncClient, world: World
) -> None:
    headers = bearer(world.read_key)
    project = world.workspace.project_id

    detail = await client.get(
        f"/api/v1/projects/{project}/traces/{world.trace_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["trace_id"] == world.trace_id
    assert len(detail.json()["spans"]) == 1

    overview = await client.get(f"/api/v1/projects/{project}/metrics/overview", headers=headers)
    assert overview.json()["current"]["traces"] == 1
    models = await client.get(f"/api/v1/projects/{project}/metrics/models", headers=headers)
    assert [row["model"] for row in models.json()] == ["gpt-4o-mini"]
    sessions = await client.get(f"/api/v1/projects/{project}/sessions", headers=headers)
    assert [item["session_id"] for item in sessions.json()["items"]] == ["s1"]
    filters = await client.get(f"/api/v1/projects/{project}/filters", headers=headers)
    assert filters.json()["environments"] == ["prod"]


@pytest.mark.parametrize("template", READ_ROUTES)
async def test_traces_read_key_gets_404_for_other_project(
    client: httpx.AsyncClient, world: World, template: str
) -> None:
    path = world.path(template, project_id=world.other_project_id, trace=world.other_trace_id)

    response = await client.get(path, headers=bearer(world.read_key))

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    # The same owner can read it with a session, so the 404 is the key's doing.
    assert (await world.owner.get(path)).status_code == 200


@pytest.mark.parametrize("project", ["00000000-0000-4000-8000-000000000000", "not-a-uuid"])
async def test_a_read_key_gets_the_same_404_for_a_project_that_does_not_exist(
    client: httpx.AsyncClient, world: World, project: str
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project}/traces", headers=bearer(world.read_key)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_a_read_key_cannot_read_the_other_projects_trace_through_its_own(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(
        f"/api/v1/projects/{world.workspace.project_id}/traces/{world.other_trace_id}",
        headers=bearer(world.read_key),
    )

    assert response.status_code == 404


@pytest.mark.parametrize("template", READ_ROUTES)
async def test_a_key_without_traces_read_cannot_read(
    client: httpx.AsyncClient, world: World, template: str
) -> None:
    # The workspace's own key is the default, `ingest:write` only.
    response = await client.get(world.path(template), headers=bearer(world.workspace.api_key))

    assert response.status_code == 403
    assert response.json()["code"] == "KEY_SCOPE"


async def test_a_key_without_traces_read_gets_404_for_another_project(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(
        f"/api/v1/projects/{world.other_project_id}/traces",
        headers=bearer(world.workspace.api_key),
    )

    assert response.status_code == 404


def _non_read_requests(world: World) -> list[tuple[str, str, dict[str, Any] | None]]:
    project = world.workspace.project_id
    org = world.workspace.org_id
    return [
        ("GET", f"/api/v1/projects/{project}", None),
        ("PATCH", f"/api/v1/projects/{project}", {"retention_days": 7}),
        ("GET", f"/api/v1/projects/{project}/keys", None),
        ("POST", f"/api/v1/projects/{project}/keys", {"name": "from a key"}),
        ("DELETE", f"/api/v1/projects/{project}/keys/{uuid.uuid4()}", None),
        ("GET", f"/api/v1/projects/{project}/onboarding", None),
        ("GET", f"/api/v1/orgs/{org}", None),
        ("GET", f"/api/v1/orgs/{org}/members", None),
        ("GET", f"/api/v1/orgs/{org}/audit", None),
        ("POST", f"/api/v1/orgs/{org}/projects", {"name": "from a key"}),
        ("POST", "/api/v1/orgs", {"name": "from a key"}),
        ("GET", "/api/v1/auth/me", None),
        ("GET", "/api/v1/auth/sessions", None),
        ("POST", "/api/v1/auth/logout", None),
    ]


async def test_api_key_on_non_read_route_is_key_scope(
    client: httpx.AsyncClient, world: World
) -> None:
    mismatches = []
    for method, path, body in _non_read_requests(world):
        response = await client.request(method, path, json=body, headers=bearer(world.read_key))
        if response.status_code != 403 or response.json()["code"] != "KEY_SCOPE":
            mismatches.append(f"{method} {path}: {response.status_code} {response.text}")
    assert mismatches == []


async def test_a_key_on_a_non_read_route_changes_nothing(
    client: httpx.AsyncClient, world: World
) -> None:
    project = world.workspace.project_id
    for method, path, body in _non_read_requests(world):
        await client.request(method, path, json=body, headers=bearer(world.read_key))

    keys = (await world.owner.get(f"/api/v1/projects/{project}/keys")).json()
    assert len(keys) == 2
    projects = (await world.owner.get(f"/api/v1/orgs/{world.workspace.org_id}/projects")).json()
    assert len(projects) == 2
    me = (await world.owner.get("/api/v1/auth/me")).json()
    assert len(me["memberships"]) == 1


async def test_bearer_with_a_session_cookie_is_authenticated_as_the_key(
    world: World,
) -> None:
    """The owner's cookie would allow all of this; the bearer key must win and be limited."""
    cookie_client = world.owner.http
    other = f"/api/v1/projects/{world.other_project_id}/traces"
    assert (await cookie_client.get(other)).status_code == 200  # the session alone may read it

    as_key = await cookie_client.get(other, headers=bearer(world.read_key))
    assert as_key.status_code == 404

    me = await cookie_client.get("/api/v1/auth/me", headers=bearer(world.read_key))
    assert me.status_code == 403
    assert me.json()["code"] == "KEY_SCOPE"


async def test_bearer_with_a_session_cookie_and_no_csrf_header_is_not_a_csrf_failure(
    world: World,
) -> None:
    cookie_client = world.owner.http
    assert world.owner.http.cookies.get("spl_csrf")  # the browser has a CSRF cookie...

    # ...but this request does not send the matching header, as a script holding a key would not.
    response = await cookie_client.post(
        f"/api/v1/projects/{world.workspace.project_id}/keys",
        json={"name": "from a key"},
        headers=bearer(world.read_key),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "KEY_SCOPE"  # not CSRF_FAILED, and not a created key
    keys = (await world.owner.get(f"/api/v1/projects/{world.workspace.project_id}/keys")).json()
    assert len(keys) == 2


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_bearer_requests_skip_the_origin_check(
    bare_client: httpx.AsyncClient, world: World, origin: str | None
) -> None:
    headers = bearer(world.read_key)
    if origin is not None:
        headers["Origin"] = origin

    response = await bare_client.post(
        f"/api/v1/projects/{world.workspace.project_id}/keys",
        json={"name": "from a key"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "KEY_SCOPE"  # not ORIGIN_NOT_ALLOWED


async def test_requests_without_authorization_still_need_an_allowed_origin(
    bare_client: httpx.AsyncClient, world: World
) -> None:
    response = await bare_client.post("/api/v1/auth/logout")

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer sk-not-one-of-ours",
        "Bearer spl_live_",
        "Bearer spl_live_abcdefghijkl_short",
        f"Bearer spl_live_{'a' * 12}_{'a' * 32}",  # well formed, but no such key
        "Bearer",
        "bearer sk-not-one-of-ours",
        "BEARER",
    ],
)
async def test_a_bad_bearer_never_falls_back_to_the_cookie(
    world: World, authorization: str
) -> None:
    cookie_client = world.owner.http
    assert (await cookie_client.get("/api/v1/auth/me")).status_code == 200

    for path in ("/api/v1/auth/me", f"/api/v1/projects/{world.workspace.project_id}/traces"):
        response = await cookie_client.get(path, headers={"Authorization": authorization})
        assert response.status_code == 401, (path, response.text)
        assert response.json()["code"] == "UNAUTHORIZED"

    # The same holds for an unsafe method with a valid CSRF header and Origin.
    unsafe = await cookie_client.post(
        f"/api/v1/projects/{world.workspace.project_id}/keys",
        json={"name": "no fallback"},
        headers={**world.owner._headers(), "Authorization": authorization},
    )
    assert unsafe.status_code == 401
    keys = (await world.owner.get(f"/api/v1/projects/{world.workspace.project_id}/keys")).json()
    assert len(keys) == 2


async def test_a_revoked_read_key_is_rejected(client: httpx.AsyncClient, world: World) -> None:
    keys_url = f"/api/v1/projects/{world.workspace.project_id}/keys"
    listed = (await world.owner.get(keys_url)).json()
    read_id = next(key["id"] for key in listed if key["scopes"] == ["traces:read"])
    assert (await world.owner.delete(f"{keys_url}/{read_id}")).status_code == 204

    response = await client.get(
        f"/api/v1/projects/{world.workspace.project_id}/traces", headers=bearer(world.read_key)
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_reading_with_a_key_records_its_last_use(
    client: httpx.AsyncClient, world: World
) -> None:
    keys_url = f"/api/v1/projects/{world.workspace.project_id}/keys"

    def read_key_entry(keys: list[dict[str, Any]]) -> dict[str, Any]:
        return next(key for key in keys if key["scopes"] == ["traces:read"])

    assert read_key_entry((await world.owner.get(keys_url)).json())["last_used_at"] is None

    traces_url = f"/api/v1/projects/{world.workspace.project_id}/traces"
    assert (await client.get(traces_url, headers=bearer(world.read_key))).status_code == 200
    first = read_key_entry((await world.owner.get(keys_url)).json())["last_used_at"]
    assert first is not None

    # A second read inside the same minute does not write again.
    assert (await client.get(traces_url, headers=bearer(world.read_key))).status_code == 200
    assert read_key_entry((await world.owner.get(keys_url)).json())["last_used_at"] == first


async def test_a_refused_read_still_records_that_the_key_was_used(
    client: httpx.AsyncClient, world: World
) -> None:
    keys_url = f"/api/v1/projects/{world.workspace.project_id}/keys"
    other = f"/api/v1/projects/{world.other_project_id}/traces"
    assert (await client.get(other, headers=bearer(world.read_key))).status_code == 404

    listed = (await world.owner.get(keys_url)).json()
    entry = next(key for key in listed if key["scopes"] == ["traces:read"])
    assert entry["last_used_at"] is not None


async def test_sessions_still_work_as_before(world: World) -> None:
    for template in READ_ROUTES:
        response = await world.owner.get(world.path(template))
        assert response.status_code == 200, (template, response.text)


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
async def test_the_bearer_scheme_is_case_insensitive(world: World, scheme: str) -> None:
    """The cookie is dropped and the key used, whatever the case of the scheme."""
    other = f"/api/v1/projects/{world.other_project_id}/traces"
    assert (await world.owner.http.get(other)).status_code == 200  # the session alone may read it

    as_key = await world.owner.http.get(
        other, headers={"Authorization": f"{scheme} {world.read_key}"}
    )
    own = await world.owner.http.get(
        f"/api/v1/projects/{world.workspace.project_id}/traces",
        headers={"Authorization": f"{scheme} {world.read_key}"},
    )

    assert as_key.status_code == 404
    assert own.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    ["Basic dXNlcjpwYXNz", "Negotiate YIIBsg==", "Digest username=u", "Token abc", ""],
)
async def test_another_authorization_scheme_is_not_a_credential_for_the_dashboard(
    world: World, authorization: str
) -> None:
    """Browsers attach Basic and Negotiate behind an authenticating proxy; the cookie decides."""
    http = world.owner.http
    headers = {"Authorization": authorization}
    keys_url = f"/api/v1/projects/{world.workspace.project_id}/keys"

    me = await http.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@example.com"

    # Unsafe requests keep their CSRF check and their Origin check, and succeed with both.
    no_csrf = await http.post(keys_url, json={"name": "no csrf"}, headers=headers)
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "CSRF_FAILED"
    created = await http.post(
        keys_url, json={"name": "via cookie"}, headers={**world.owner._headers(), **headers}
    )
    assert created.status_code == 201, created.text


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
@pytest.mark.parametrize("authorization", ["Basic dXNlcjpwYXNz", "Negotiate YIIBsg==", ""])
async def test_another_authorization_scheme_does_not_switch_off_the_origin_check(
    bare_client: httpx.AsyncClient, world: World, origin: str | None, authorization: str
) -> None:
    """Login CSRF: an ambient Basic credential must not make a cross-site login post acceptable."""
    headers = {"Authorization": authorization}
    if origin is not None:
        headers["Origin"] = origin

    response = await bare_client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct horse battery"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert "set-cookie" not in response.headers
