"""The authorization matrix harness: rows, route inventory and the per-role checks.

A `Row` names one route and the permission it is meant to be guarded by. That is the test's own
statement of intent; two things hold it to the code:

* `declared_permission` reads the permission the route actually passes to `require(...)`, and a
  test compares it with the row, so loosening or tightening a guard fails here;
* the role outcomes are derived from the permission table (`app.core.permissions`), never typed
  by hand: a role that holds the permission must get through, one that does not must be refused.

Every request is safe to repeat. Authorization runs as a route dependency, before the body is
validated and before any handler code, so each call sends a body that cannot be valid (`{}`) and
ids that exist nowhere: a role that is let in gets a 422 (invalid body) or a handler 404 (no such
resource), a role that is refused gets 403, and a non-member gets 404. Nothing is created or
changed. The one route that acts with no body or id is the gateway cache purge, which is
idempotent.

`ROLES` run against one workspace: owner, admin, member, viewer, and an `outsider` who owns a
different organization and is no member of this one.

A role that holds the permission must get one of `ALLOWED_STATUSES`: a success, or 422 for the
body that cannot be valid. On a route that names a resource besides the org or project, 404 is
allowed too, because the resource does not exist. The known blind spot: there, "wrongly treated
as a non-member" and "no such resource" are the same 404. The list routes of each router, the
static guard tests and the cross-org controls (`test_cross_org_resources.py`) cover it; do not
widen the allowed set further.
"""

import inspect
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.routing import APIRoute

from app.api import v1
from app.core.permissions import Permission, has
from app.db.models import MembershipRole
from tests.helpers import Browser, Workspace

ROLES = ("owner", "admin", "member", "viewer", "outsider")
SKIPPED_METHODS = {"HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# `{name:path}`, as Starlette spells a path converter.
PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")
CONVERTER = re.compile(r"\{(\w+):\w+\}")

# What a role that holds the permission may get from the safe-repeat request below. Anything
# else (a 429, a 409, a 400, a 5xx) means the request did not reach the handler as intended.
ALLOWED_STATUSES = frozenset({200, 201, 202, 204, 422})

Route = tuple[str, str]  # (METHOD, "/api/v1/..." exactly as the router declares it)
Actors = tuple[Workspace, dict[str, Browser]]


@dataclass(frozen=True)
class Row:
    """One route and the permission that guards it.

    `then` is a second permission the handler itself checks once `require()` has let the caller
    in (removing a member needs `org:read` to reach the route, `member:manage` unless it is
    yourself). The role outcome follows `then` when there is one. `also` adds statuses a role
    that is let in may get besides `ALLOWED_STATUSES`, for a handler with a documented refusal
    of its own that comes before the resource lookup (name it in a comment on the row).
    """

    method: str
    path: str  # the router's path, starting with "/api/v1"
    permission: Permission
    then: Permission | None = None
    also: frozenset[int] = frozenset()

    @property
    def route(self) -> Route:
        return (self.method, self.path)

    @property
    def needed(self) -> Permission:
        return self.then or self.permission

    @property
    def has_resource(self) -> bool:
        """Whether the path names a resource besides the org or project (a handler can 404)."""
        return bool(set(PARAMETER.findall(self.path)) - {"org_id", "project_id"})

    def label(self) -> str:
        return f"{self.method} {self.path}"


def declared_routes() -> dict[Route, APIRoute]:
    """Every `/api/v1` route the domain routers serve, keyed by (method, path)."""
    found: dict[Route, APIRoute] = {}
    for router in v1.DOMAIN_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            for method in (route.methods or set()) - SKIPPED_METHODS:
                found[(method, f"/api/v1{route.path}")] = route
    return found


def dependency_calls(route: APIRoute) -> Iterator[Callable[..., Any]]:
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        for sub in dependant.dependencies:
            if sub.call is not None:
                yield sub.call
            pending.append(sub)


def _require_variables(route: APIRoute) -> dict[str, Any]:
    """What the route's `require(...)` guard was built with (its closure), or {} if it has none.

    `require()` returns a closure; the permission and `allow_api_key` it was built with are that
    closure's captured variables. A route guarded by `idempotent(require(X))` is found through
    its dependency tree.
    """
    found: list[Mapping[str, Any]] = []
    for call in dependency_calls(route):
        if call.__qualname__ == "require.<locals>.dependency":
            found.append(inspect.getclosurevars(call).nonlocals)
    assert len(found) <= 1, (route.path, found)
    return dict(found[0]) if found else {}


def declared_permission(route: APIRoute) -> Permission | None:
    """The permission the route passes to `require(...)`, or None if it has no such guard."""
    captured = _require_variables(route).get("permission")
    return captured if isinstance(captured, Permission) else None


def declared_key_readable() -> set[Route]:
    """The routes whose `require(...)` guard lets an API key in (`allow_api_key=True`)."""
    return {
        key
        for key, route in declared_routes().items()
        if _require_variables(route).get("allow_api_key") is True
    }


def fill(template: str, workspace: Workspace) -> str:
    """The path with real org and project ids and ids that exist nowhere for the rest."""
    values = {
        "org_id": workspace.org_id,
        "project_id": workspace.project_id,
        "trace_id": "a" * 32,
        "external_user_id": "nobody",
    }

    def value(match: re.Match[str]) -> str:
        return values.get(match.group(1)) or str(uuid.uuid4())

    return PARAMETER.sub(value, template)


async def call(
    client: httpx.AsyncClient, method: str, path: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    """One request with a body that cannot be valid (none for a read)."""
    body: dict[str, Any] | None = {} if method in WRITE_METHODS else None
    return await client.request(method, path, headers=headers, json=body)


async def send(browser: Browser, method: str, path: str) -> httpx.Response:
    """A signed-in request: the cookies and the CSRF header a browser would send."""
    token = browser.http.cookies.get("spl_csrf")
    return await call(browser.http, method, path, {"X-CSRF-Token": token} if token else None)


def problem_code(response: httpx.Response) -> str | None:
    """The stable `code` of a problem+json response, or None for any other body."""
    if not response.content or "json" not in response.headers.get("content-type", ""):
        return None
    body = response.json()
    return body.get("code") if isinstance(body, dict) else None


def role_problem(role: str, row: Row, status: int) -> str | None:
    """Why `status` is wrong for `role` on `row`, or None when it is right."""
    if role == "outsider":
        return None if status == 404 else "a non-member must get 404"
    if not has(MembershipRole(role), row.needed):
        return None if status == 403 else f"a role without {row.needed} must get 403"
    allowed = ALLOWED_STATUSES | row.also | ({404} if row.has_resource else set())
    if status in allowed:
        return None
    if status == 404:
        return "a role with the permission got the non-member 404"
    return f"a role with {row.needed} must get past authorization, one of {sorted(allowed)}"


async def role_mismatches(
    rows: tuple[Row, ...], workspace: Workspace, actors: dict[str, Browser]
) -> list[str]:
    """Run every row as every role; the lines that disagree with the permission table."""
    mismatches: list[str] = []
    for row in rows:
        path = fill(row.path, workspace)
        for role in ROLES:
            status = (await send(actors[role], row.method, path)).status_code
            problem = role_problem(role, row, status)
            if problem:
                mismatches.append(f"{row.label()} as {role}: got {status}, {problem}")
    return mismatches
