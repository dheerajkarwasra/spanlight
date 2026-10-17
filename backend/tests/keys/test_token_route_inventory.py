"""What a personal access token may do, checked against every `/api/v1` route.

Like the API key inventory, this is built from the routers, so a route added later is covered the
day it exists. Each route is sorted by the dependency it declares:

* a route that needs a session (`CurrentSession`) or no sign-in at all (`ANONYMOUS`) answers a
  token with 403 `SESSION_REQUIRED`;
* `GET /auth/me` (`CurrentUser`) takes a token;
* every other route is guarded by `require(permission)`: a token reaches it as its user, with
  the user's memberships, and a `read` token only where the permission is a read.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

from app.api import v1
from app.api.deps import ANONYMOUS, current_session, current_user
from tests.helpers import Browser, bearer, create_token, expire_token
from tests.key_readable import KEY_READABLE
from tests.keys.conftest import World
from tests.keys.test_key_route_inventory import (
    SKIPPED_METHODS,
    _call,
    _declared_routes,
    _fill,
    _params,
)

# Every test here walks all routes with one credential: see `unthrottled_reads`.
pytestmark = pytest.mark.usefixtures("unthrottled_reads", "release_and_user_data")

Route = tuple[str, str]

# Guarded routes on which a read token is refused (403 `TOKEN_SCOPE`) even for an owner: their
# permission is classed `write`, or they change something under a `read` permission (leaving an
# organization needs only `org:read`, but it is a DELETE). Adding a route means saying here which
# kind it is: a decision for review, not for a refactor. Not all of these mutate: listing invites
# needs `member:manage`, which is a write permission.
WRITE_GUARDED: set[Route] = {
    ("PATCH", "/api/v1/orgs/{org_id}"),
    ("DELETE", "/api/v1/orgs/{org_id}"),
    ("PATCH", "/api/v1/orgs/{org_id}/members/{user_id}"),
    ("DELETE", "/api/v1/orgs/{org_id}/members/{user_id}"),
    ("GET", "/api/v1/orgs/{org_id}/invites"),
    ("POST", "/api/v1/orgs/{org_id}/invites"),
    ("DELETE", "/api/v1/orgs/{org_id}/invites/{invite_id}"),
    ("POST", "/api/v1/orgs/{org_id}/projects"),
    ("PATCH", "/api/v1/projects/{project_id}"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    ("POST", "/api/v1/projects/{project_id}/keys"),
    ("DELETE", "/api/v1/projects/{project_id}/keys/{key_id}"),
    # Phases 2-5: gateway, alerts, budgets, the Doctor, prompts, scores, datasets, annotation,
    # evaluators and experiments. Every one is a non-GET method, refused to a read token whatever
    # its permission. Some do not mutate (`alert-rules/preview`, `insights/{id}/explain`, an
    # evaluator test) but run work a read-only credential must not start.
    ("DELETE", "/api/v1/orgs/{org_id}/alert-channels/{channel_id}"),
    ("DELETE", "/api/v1/orgs/{org_id}/billing-sources/{source_id}"),
    ("DELETE", "/api/v1/orgs/{org_id}/credentials/{credential_id}"),
    ("DELETE", "/api/v1/orgs/{org_id}/price-overrides/{override_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/alert-rules/{rule_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/budgets/{budget_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/datasets/{dataset_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/datasets/{dataset_id}/items/{item_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/evaluators/{evaluator_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/gateway/fault-profiles/{profile_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/gateway/keys/{key_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/gateway/routes/{route_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/insights/{insight_id}/feedback"),
    ("DELETE", "/api/v1/projects/{project_id}/prompts/{name:path}"),
    ("DELETE", "/api/v1/projects/{project_id}/prompts/{name:path}/labels/{label}"),
    ("DELETE", "/api/v1/projects/{project_id}/score-configs/{config_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/scores/{score_id}"),
    ("PATCH", "/api/v1/orgs/{org_id}/alert-channels/{channel_id}"),
    ("PATCH", "/api/v1/orgs/{org_id}/billing-sources/{source_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/alert-rules/{rule_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/budgets/{budget_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/datasets/{dataset_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/datasets/{dataset_id}/items/{item_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/evaluators/{evaluator_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/gateway/fault-profiles/{profile_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/gateway/keys/{key_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/score-configs/{config_id}"),
    ("POST", "/api/v1/orgs/{org_id}/alert-channels"),
    ("POST", "/api/v1/orgs/{org_id}/alert-channels/{channel_id}/deliveries/{delivery_id}/retry"),
    ("POST", "/api/v1/orgs/{org_id}/alert-channels/{channel_id}/test"),
    ("POST", "/api/v1/orgs/{org_id}/billing-sources"),
    ("POST", "/api/v1/orgs/{org_id}/billing-sources/{source_id}/refresh"),
    ("POST", "/api/v1/orgs/{org_id}/billing-sources/{source_id}/rotate"),
    ("POST", "/api/v1/orgs/{org_id}/billing-sources/{source_id}/upload"),
    ("POST", "/api/v1/orgs/{org_id}/credentials"),
    ("POST", "/api/v1/orgs/{org_id}/credentials/{credential_id}/check"),
    ("POST", "/api/v1/orgs/{org_id}/credentials/{credential_id}/rotate"),
    ("POST", "/api/v1/orgs/{org_id}/price-overrides"),
    ("POST", "/api/v1/projects/{project_id}/alert-events/{event_id}/acknowledge"),
    ("POST", "/api/v1/projects/{project_id}/alert-rules"),
    ("POST", "/api/v1/projects/{project_id}/alert-rules/preview"),
    ("POST", "/api/v1/projects/{project_id}/alert-rules/{rule_id}/mute"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}/claim"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}/items"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}/items/{item_id}/complete"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}/items/{item_id}/release"),
    ("POST", "/api/v1/projects/{project_id}/annotation-queues/{queue_id}/items/{item_id}/skip"),
    ("POST", "/api/v1/projects/{project_id}/budgets"),
    ("POST", "/api/v1/projects/{project_id}/datasets"),
    ("POST", "/api/v1/projects/{project_id}/datasets/{dataset_id}/items"),
    ("POST", "/api/v1/projects/{project_id}/datasets/{dataset_id}/items/from-traces"),
    ("POST", "/api/v1/projects/{project_id}/datasets/{dataset_id}/items/upload"),
    ("POST", "/api/v1/projects/{project_id}/evaluators"),
    ("POST", "/api/v1/projects/{project_id}/evaluators/{evaluator_id}/test"),
    ("POST", "/api/v1/projects/{project_id}/experiments"),
    ("POST", "/api/v1/projects/{project_id}/experiments/{experiment_id}/runs"),
    ("POST", "/api/v1/projects/{project_id}/exports"),
    ("POST", "/api/v1/projects/{project_id}/gateway/cache/purge"),
    ("POST", "/api/v1/projects/{project_id}/gateway/fault-profiles"),
    ("POST", "/api/v1/projects/{project_id}/gateway/keys"),
    ("POST", "/api/v1/projects/{project_id}/gateway/routes"),
    ("POST", "/api/v1/projects/{project_id}/gateway/routes/{route_id}/default"),
    ("POST", "/api/v1/projects/{project_id}/gateway/routes/{route_id}/revert"),
    ("POST", "/api/v1/projects/{project_id}/insights/{insight_id}/acknowledge"),
    ("POST", "/api/v1/projects/{project_id}/insights/{insight_id}/explain"),
    ("POST", "/api/v1/projects/{project_id}/insights/{insight_id}/mute"),
    ("POST", "/api/v1/projects/{project_id}/insights/{insight_id}/resolve"),
    ("POST", "/api/v1/projects/{project_id}/insights/{insight_id}/unmute"),
    ("POST", "/api/v1/projects/{project_id}/playground/runs"),
    ("POST", "/api/v1/projects/{project_id}/prompts"),
    ("POST", "/api/v1/projects/{project_id}/prompts/{name:path}/versions"),
    ("POST", "/api/v1/projects/{project_id}/score-configs"),
    ("POST", "/api/v1/projects/{project_id}/traces/{trace_id}/scores"),
    ("PUT", "/api/v1/projects/{project_id}/gateway/routes/{route_id}"),
    ("PUT", "/api/v1/projects/{project_id}/insights/{insight_id}/feedback"),
    ("PUT", "/api/v1/projects/{project_id}/prompts/{name:path}/labels/{label}"),
}

# The refusals a credential or its scope can cause. A route that merely rejects a made-up body
# (422) or a made-up id (404) is not in this set, which is what "the token got through" means.
REFUSALS = {"UNAUTHORIZED", "TOKEN_EXPIRED", "TOKEN_SCOPE", "SESSION_REQUIRED", "KEY_SCOPE"}


def _dependencies(route: APIRoute) -> set[Callable[..., Any]]:
    found: set[Callable[..., Any]] = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        for sub in dependant.dependencies:
            if sub.call is not None:
                found.add(sub.call)
            pending.append(sub)
    return found


def _routes_by_kind() -> dict[str, set[Route]]:
    kinds: dict[str, set[Route]] = {
        "session": set(),
        "anonymous": set(),
        "user": set(),
        "guarded": set(),
    }
    anonymous_calls = {dependency.dependency for dependency in ANONYMOUS}
    for router in v1.DOMAIN_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            calls = _dependencies(route)
            if current_session in calls:
                kind = "session"
            elif calls & anonymous_calls:
                kind = "anonymous"
            elif current_user in calls:
                kind = "user"
            else:
                kind = "guarded"
                # `require()` resolves the org or project from the path; without one it raises.
                assert {"org_id", "project_id"} & set(route.param_convertors), route.path
            for method in route.methods - SKIPPED_METHODS:
                kinds[kind].add((method, f"/api/v1{route.path}"))
    return kinds


KINDS = _routes_by_kind()


def test_every_route_has_exactly_one_kind() -> None:
    # Together the kinds are every route, and no route is in two of them.
    assert set().union(*KINDS.values()) == _declared_routes()
    assert sum(len(routes) for routes in KINDS.values()) == len(_declared_routes())
    # The caller's own account, and the global price table any signed-in user may read.
    assert KINDS["user"] == {("GET", "/api/v1/auth/me"), ("GET", "/api/v1/prices")}
    assert KINDS["guarded"] >= WRITE_GUARDED
    assert KINDS["guarded"] - WRITE_GUARDED >= KEY_READABLE
    # A broken walk must not pass by finding nothing.
    assert len(KINDS["session"]) >= 10
    assert len(KINDS["anonymous"]) >= 10
    assert len(KINDS["guarded"]) >= 20


class Tokens:
    """Tokens for the routes under test: an owner's read and write, and a stranger's."""

    def __init__(self, read: str, write: str, stranger: str) -> None:
        self.read = read
        self.write = write
        self.stranger = stranger


@pytest.fixture
async def tokens(world: World, browser_factory: Any) -> Tokens:
    stranger: Browser = await browser_factory("stranger@example.com")
    return Tokens(
        read=(await create_token(world.owner, scope="read"))["token"],
        write=(await create_token(world.owner, scope="write"))["token"],
        stranger=(await create_token(stranger, scope="write"))["token"],
    )


def _outcome(response: httpx.Response) -> tuple[int, str | None]:
    # Every refusal is problem+json. A route that streams another format (the audit log's CSV
    # export) answered the request, so its body has no code to read.
    if not response.headers.get("content-type", "").startswith(
        ("application/json", "application/problem+json")
    ):
        return response.status_code, None
    body = response.json() if response.content else None
    return response.status_code, body.get("code") if isinstance(body, dict) else None


async def test_a_token_gets_session_required_on_session_only_and_anonymous_routes(
    bare_client: httpx.AsyncClient, world: World, tokens: Tokens
) -> None:
    mismatches: list[str] = []
    for method, template in sorted(KINDS["session"] | KINDS["anonymous"]):
        path = _fill(
            template, world, project_id=world.workspace.project_id, trace_id=world.trace_id
        )
        for scope, token in (("read", tokens.read), ("write", tokens.write)):
            response = await _call(bare_client, method, path, **bearer(token))
            if _outcome(response) != (403, "SESSION_REQUIRED"):
                mismatches.append(f"{method} {template} ({scope}): {_outcome(response)}")
    assert mismatches == []
    assert len(bare_client.cookies) == 0


async def test_me_takes_a_token_of_either_scope(
    bare_client: httpx.AsyncClient, world: World, tokens: Tokens
) -> None:
    for token in (tokens.read, tokens.write, tokens.stranger):
        response = await bare_client.get("/api/v1/auth/me", headers=bearer(token))
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers


async def test_a_token_reaches_every_guarded_route_as_its_user_within_its_scope(
    bare_client: httpx.AsyncClient, world: World, tokens: Tokens
) -> None:
    mismatches: list[str] = []
    for method, template in sorted(KINDS["guarded"]):
        own = _fill(template, world, project_id=world.workspace.project_id, trace_id=world.trace_id)
        sibling = _fill(
            template, world, project_id=world.other_project_id, trace_id=world.other_trace_id
        )
        write_only = (method, template) in WRITE_GUARDED

        read_own = await _call(bare_client, method, own, **bearer(tokens.read))
        write_own = await _call(bare_client, method, own, **bearer(tokens.write))
        stranger = await _call(bare_client, method, own, **bearer(tokens.stranger))
        stranger_write = await _call(bare_client, method, sibling, **bearer(tokens.stranger))

        checks = {
            "read token": _outcome(read_own)[1] == "TOKEN_SCOPE"
            if write_only
            else _outcome(read_own)[1] not in REFUSALS | {"FORBIDDEN", "INTERNAL_ERROR"},
            # Getting through must mean the route answered, not that it crashed.
            "write token": _outcome(write_own)[1] not in REFUSALS | {"FORBIDDEN", "INTERNAL_ERROR"},
            # Not a member of the org: the same 404 as for an org that does not exist, whatever
            # the route and whatever the scope. It comes before the scope check.
            "stranger": _outcome(stranger) == (404, "NOT_FOUND"),
            "stranger on the sibling project": _outcome(stranger_write) == (404, "NOT_FOUND"),
        }
        mismatches.extend(
            f"{method} {template}: {name} ({_outcome(read_own)} / {_outcome(write_own)})"
            for name, ok in checks.items()
            if not ok
        )
    assert mismatches == []


async def test_a_token_reads_everything_a_read_key_can_and_more(
    bare_client: httpx.AsyncClient, world: World, tokens: Tokens
) -> None:
    # The routes open to a `traces:read` key are plain `project:read` routes, so a read token
    # reaches them in every project its user belongs to, not only in one.
    for method, template in sorted(KEY_READABLE):
        for project_id, trace_id in (
            (world.workspace.project_id, world.trace_id),
            (world.other_project_id, world.other_trace_id),
        ):
            path = _fill(template, world, project_id=project_id, trace_id=trace_id)
            response = await bare_client.request(
                method, path, params=_params(path), headers=bearer(tokens.read)
            )
            assert response.status_code == 200, (path, response.text)


@pytest.mark.parametrize("state", ["revoked", "expired"])
async def test_a_dead_token_is_refused_on_every_route(
    bare_client: httpx.AsyncClient, world: World, session_factory: Any, state: str
) -> None:
    created = await create_token(world.owner, expires_at="2099-01-01T00:00:00Z")
    if state == "revoked":
        deleted = await world.owner.delete(f"/api/v1/auth/tokens/{created['id']}")
        assert deleted.status_code == 204
        expected = (401, "UNAUTHORIZED")
    else:
        await expire_token(session_factory, created["id"])
        expected = (401, "TOKEN_EXPIRED")

    mismatches: list[str] = []
    for method, template in sorted(_declared_routes()):
        path = _fill(
            template, world, project_id=world.workspace.project_id, trace_id=world.trace_id
        )
        response = await _call(bare_client, method, path, **bearer(created["token"]))
        if _outcome(response) != expected:
            mismatches.append(f"{method} {template}: {_outcome(response)}")
    assert mismatches == []


@pytest.mark.parametrize(
    "authorization",
    [f"Bearer spl_pat_{'a' * 12}_{'a' * 32}", "Bearer spl_pat_x"],
)
async def test_a_bearer_that_is_not_a_known_token_is_unauthorized_everywhere(
    bare_client: httpx.AsyncClient, world: World, authorization: str
) -> None:
    mismatches: list[str] = []
    for method, template in sorted(_declared_routes()):
        path = _fill(
            template, world, project_id=world.workspace.project_id, trace_id=world.trace_id
        )
        response = await _call(bare_client, method, path, Authorization=authorization)
        if _outcome(response) != (401, "UNAUTHORIZED"):
            mismatches.append(f"{method} {template}: {_outcome(response)}")
    assert mismatches == []
