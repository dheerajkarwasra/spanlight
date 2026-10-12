"""Every `/api/v1` route refuses an API key, except the ones that opt in.

The inventory is built from the routers, not from a list in this file, so a route added later is
covered the day it exists. A route that forgets to say what it does with a key fails here.
"""

import re
import uuid

import httpx
import pytest
from fastapi.routing import APIRoute

from app.api import v1
from scripts.update_openapi import build_app
from tests.helpers import bearer
from tests.keys.conftest import END_USER, RELEASES, World

# Every test here walks all routes with one credential: see `unthrottled_reads`.
pytestmark = pytest.mark.usefixtures("unthrottled_reads", "release_and_user_data")

SKIPPED_METHODS = {"HEAD", "OPTIONS"}
# A path parameter with a Starlette converter, such as `{name:path}`.
CONVERTER = re.compile(r"\{(\w+):\w+\}")

# The only routes an API key may use, and only with `traces:read`, for its own project. Adding to
# this set is the decision to let keys into a route: it belongs in a review, not in a refactor.
KEY_READABLE = {
    ("GET", "/api/v1/projects/{project_id}/traces"),
    ("GET", "/api/v1/projects/{project_id}/traces/{trace_id}"),
    ("GET", "/api/v1/projects/{project_id}/sessions"),
    ("GET", "/api/v1/projects/{project_id}/filters"),
    ("GET", "/api/v1/projects/{project_id}/metrics/overview"),
    ("GET", "/api/v1/projects/{project_id}/metrics/timeseries"),
    ("GET", "/api/v1/projects/{project_id}/metrics/models"),
    # The Phase 1 spec lists it with the metrics routes (docs/api-deviations.md, "Prices").
    ("GET", "/api/v1/projects/{project_id}/unpriced-models"),
    # Phase 4: releases and end users, for a CI job that checks a release after a deploy
    # (docs/api-deviations.md, "Releases" and "Users").
    ("GET", "/api/v1/projects/{project_id}/releases"),
    ("GET", "/api/v1/projects/{project_id}/releases/compare"),
    ("GET", "/api/v1/projects/{project_id}/users"),
    ("GET", "/api/v1/projects/{project_id}/users/{external_user_id:path}"),
}


def _declared_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for router in v1.DOMAIN_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            routes.update(
                (method, f"/api/v1{route.path}") for method in route.methods - SKIPPED_METHODS
            )
    return routes


def _without_converters(path: str) -> str:
    """`/prompts/{name:path}` as OpenAPI spells it: `/prompts/{name}`."""
    return CONVERTER.sub(r"{\1}", path)


def _documented_routes() -> set[tuple[str, str]]:
    paths = build_app().openapi()["paths"]
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/")
        for method in operations
    }


def test_the_inventory_matches_what_the_api_serves() -> None:
    declared = _declared_routes()

    assert len(declared) >= 40  # a broken walk must not pass by finding nothing
    assert declared >= KEY_READABLE
    # OpenAPI drops path converters (`{name:path}` is documented as `{name}`).
    assert {(method, _without_converters(path)) for method, path in declared} == (
        _documented_routes()
    )


def _fill(template: str, world: World, *, project_id: str, trace_id: str) -> str:
    values = {
        "project_id": project_id,
        "org_id": world.workspace.org_id,
        "trace_id": trace_id,
        "provider": "github",
        "external_user_id": END_USER,
    }
    path = _without_converters(template)
    names = [part[1:-1] for part in path.split("/") if part.startswith("{")]
    return path.format(**{name: values.get(name, str(uuid.uuid4())) for name in names})


def _is_key_scope(response: httpx.Response) -> bool:
    body = response.json() if response.content else None
    return (
        response.status_code == 403 and isinstance(body, dict) and body.get("code") == "KEY_SCOPE"
    )


def _params(path: str) -> dict[str, str] | None:
    """The query a route needs before it looks at anything else."""
    if path.endswith("/invites/preview"):
        return {"token": "x"}
    if path.endswith("/releases/compare"):
        return dict(zip(("a", "b"), RELEASES, strict=True))
    return None


async def _call(
    client: httpx.AsyncClient, method: str, path: str, **headers: str
) -> httpx.Response:
    response = await client.request(
        method,
        path,
        json={} if method in {"POST", "PUT", "PATCH", "DELETE"} else None,
        params=_params(path),
        headers=headers,
    )
    assert "set-cookie" not in response.headers, (method, path)
    return response


async def test_an_api_key_is_refused_everywhere_except_the_key_readable_routes(
    bare_client: httpx.AsyncClient, world: World
) -> None:
    """With no Origin header, as a script holding a key would send it."""
    mismatches: list[str] = []
    for method, template in sorted(_declared_routes()):
        own = _fill(template, world, project_id=world.workspace.project_id, trace_id=world.trace_id)
        other = _fill(
            template, world, project_id=world.other_project_id, trace_id=world.other_trace_id
        )
        read_own = await _call(bare_client, method, own, **bearer(world.read_key))
        read_other = await _call(bare_client, method, other, **bearer(world.read_key))
        ingest_own = await _call(bare_client, method, own, **bearer(world.workspace.api_key))

        if (method, template) in KEY_READABLE:
            # Its own project with `traces:read`; a 404 for any other project, whatever the scope.
            checks = {
                "read key on its own project": read_own.status_code == 200,
                "read key on another project": read_other.status_code == 404,
                "ingest-only key on its own project": _is_key_scope(ingest_own),
            }
        else:
            checks = {
                "read key on its own project": _is_key_scope(read_own),
                "read key on another project": _is_key_scope(read_other),
                "ingest-only key on its own project": _is_key_scope(ingest_own),
            }
        mismatches.extend(f"{method} {template}: {name}" for name, ok in checks.items() if not ok)
    assert mismatches == []


@pytest.mark.parametrize(
    "authorization",
    ["Bearer sk-not-ours", "Bearer", f"Bearer spl_live_{'a' * 12}_{'a' * 32}"],
)
async def test_a_bearer_that_is_not_a_live_key_is_unauthorized_everywhere(
    bare_client: httpx.AsyncClient, world: World, authorization: str
) -> None:
    mismatches: list[str] = []
    for method, template in sorted(_declared_routes()):
        path = _fill(
            template, world, project_id=world.workspace.project_id, trace_id=world.trace_id
        )
        response = await _call(bare_client, method, path, Authorization=authorization)
        body = response.json() if response.content else None
        code = body.get("code") if isinstance(body, dict) else None
        if (response.status_code, code) != (401, "UNAUTHORIZED"):
            mismatches.append(f"{method} {template}: {response.status_code} {code}")
    assert mismatches == []
