"""No `/api/v1` route can escape the authorization matrix, or lose its guard unnoticed.

The route list comes from the routers, so a route added later fails here until it has a row in a
matrix or an entry in `NOT_IN_MATRIX` with its reason. The routers are held to the application:
a router mounted under `/api/v1` some other way than through `DOMAIN_ROUTERS` fails too.
"""

from typing import Any

from app.api.deps import ANONYMOUS, current_session, current_user
from tests.authz.gaps import NOT_IN_MATRIX
from tests.authz.matrix import (
    CONVERTER,
    PARAMETER,
    Route,
    declared_key_readable,
    declared_permission,
    declared_routes,
    dependency_calls,
)
from tests.authz.milestone_1 import CASES
from tests.authz.rows import ALL_ROWS as MATRIX_ROWS
from tests.key_readable import KEY_READABLE


def _milestone_1_routes() -> set[Route]:
    """The routes of `test_authz.CASES`, spelled as the routers declare them."""
    routes: set[Route] = set()
    for case in CASES:
        path = case.path.replace("{org}", "{org_id}").replace("{project}", "{project_id}")
        routes.add((case.method, path.replace("a" * 32, "{trace_id}")))
    return routes


def test_every_route_has_a_row_or_a_reason() -> None:
    routes = set(declared_routes())
    assert len(routes) >= 150  # a broken walk must not pass by finding nothing
    covered = _milestone_1_routes() | {row.route for row in MATRIX_ROWS} | set(NOT_IN_MATRIX)
    assert sorted(routes - covered) == []


def test_nothing_in_the_matrix_or_the_exclusions_is_stale() -> None:
    routes = set(declared_routes())
    rows = {row.route for row in MATRIX_ROWS}
    milestone_1 = _milestone_1_routes()
    assert sorted(milestone_1 - routes) == []
    assert sorted(rows - routes) == []
    assert sorted(set(NOT_IN_MATRIX) - routes) == []
    # A route is in exactly one place, so an exclusion cannot hide a route that has a row.
    places = [milestone_1, rows, set(NOT_IN_MATRIX)]
    assert sum(len(place) for place in places) == len(set().union(*places))
    assert all(NOT_IN_MATRIX.values())  # every exclusion says why


def test_a_row_names_the_permission_its_route_declares() -> None:
    routes = declared_routes()
    wrong = [
        f"{row.label()}: declares {declared_permission(routes[row.route])}, row says "
        f"{row.permission}"
        for row in MATRIX_ROWS
        if declared_permission(routes[row.route]) != row.permission
    ]
    assert wrong == []


def test_every_org_and_project_route_declares_a_require_guard() -> None:
    """A route with an org or project in its path must go through `require(permission)`.

    `require()` is what looks the membership up and, for a project, runs `SET LOCAL
    app.project_id` for row-level security. A route that skipped it would serve its tenant's rows
    to anyone, or none at all.
    """
    unguarded = []
    for (method, path), route in declared_routes().items():
        scoped = {"org_id", "project_id"} & set(PARAMETER.findall(path))
        if scoped and declared_permission(route) is None:
            unguarded.append(f"{method} {path}")
    assert unguarded == []


def test_a_route_without_an_org_or_project_has_no_require_guard() -> None:
    """The reverse: a guard with nothing to resolve would raise on every request."""
    misplaced = []
    for (method, path), route in declared_routes().items():
        if {"org_id", "project_id"} & set(PARAMETER.findall(path)):
            continue
        calls = set(dependency_calls(route))
        if declared_permission(route) is not None:
            misplaced.append(f"{method} {path}")
        # Each is either someone's own account (a session or a user) or open to anyone.
        elif not calls & ({current_session, current_user} | {d.dependency for d in ANONYMOUS}):
            misplaced.append(f"{method} {path}: no principal dependency")
    assert misplaced == []


def test_the_declared_routes_are_every_api_v1_route_the_app_serves(app: Any) -> None:
    """A router mounted under `/api/v1` outside `DOMAIN_ROUTERS` would escape every matrix.

    The application's own route table is lazy in this FastAPI, so the served routes are read from
    its OpenAPI document, which spells a `{name:path}` converter as `{name}`.
    """
    served = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/")
        for method in operations
    }
    declared = {(method, CONVERTER.sub(r"{\1}", path)) for method, path in declared_routes()}
    assert len(served) >= 150  # a broken walk must not pass by finding nothing
    assert sorted(served ^ declared) == []


def test_the_key_readable_routes_are_exactly_the_ones_that_allow_a_key() -> None:
    """The hand-written list (`tests/key_readable.py`) and the guards on the routes agree."""
    assert sorted(declared_key_readable() ^ KEY_READABLE) == []
