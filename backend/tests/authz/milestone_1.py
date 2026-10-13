"""The Milestone-1 routes and the status each role must get from each, written out by hand.

`test_authz.py` runs them with valid bodies, so a permitted role really succeeds. The newer
routes are in `rows_a.py` and `rows_b.py`, which check each role against the permission table
instead.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Case:
    method: str
    path: str
    expected: dict[str, int]
    body: dict[str, Any] | None = None


def _everyone(status: int) -> dict[str, int]:
    return {"owner": status, "admin": status, "member": status, "viewer": status, "outsider": 404}


def _at_least(role: str, success: int) -> dict[str, int]:
    order = ["viewer", "member", "admin", "owner"]
    allowed = set(order[order.index(role) :])
    return {
        **{name: success if name in allowed else 403 for name in order},
        "outsider": 404,
    }


CASES = (
    Case("GET", "/api/v1/orgs/{org}", _everyone(200)),
    Case("PATCH", "/api/v1/orgs/{org}", _at_least("admin", 200), {"name": "Renamed"}),
    Case("GET", "/api/v1/orgs/{org}/members", _everyone(200)),
    Case("GET", "/api/v1/orgs/{org}/audit", _at_least("admin", 200)),
    Case("GET", "/api/v1/orgs/{org}/invites", _at_least("admin", 200)),
    Case("POST", "/api/v1/orgs/{org}/invites", _at_least("admin", 201), {"role": "viewer"}),
    Case("GET", "/api/v1/orgs/{org}/projects", _everyone(200)),
    Case("POST", "/api/v1/orgs/{org}/projects", _at_least("admin", 201), {"name": "Another"}),
    Case("GET", "/api/v1/projects/{project}", _everyone(200)),
    Case("PATCH", "/api/v1/projects/{project}", _at_least("admin", 200), {"retention_days": 7}),
    Case("GET", "/api/v1/projects/{project}/keys", _everyone(200)),
    Case("POST", "/api/v1/projects/{project}/keys", _at_least("member", 201), {"name": "k"}),
    Case("GET", "/api/v1/projects/{project}/onboarding", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/traces", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/traces/" + "a" * 32, {**_everyone(404)}),
    Case("GET", "/api/v1/projects/{project}/sessions", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/filters", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/metrics/overview", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/metrics/timeseries", _everyone(200)),
    Case("GET", "/api/v1/projects/{project}/metrics/models", _everyone(200)),
)
