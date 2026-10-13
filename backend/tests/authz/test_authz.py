"""The authorization matrix: every role (and non-members) against every Milestone-1 route."""

from typing import Any

from tests.authz.matrix import ROLES, Actors
from tests.authz.milestone_1 import CASES


async def test_authorization_matrix(matrix_actors: Actors) -> None:
    workspace, browsers = matrix_actors
    mismatches = []
    for case in CASES:
        path = case.path.format(org=workspace.org_id, project=workspace.project_id)
        for role in ROLES:
            browser = browsers[role]
            if case.method == "GET":
                response = await browser.get(path)
            elif case.method == "POST":
                response = await browser.post(path, json=case.body)
            else:
                response = await browser.patch(path, json=case.body)
            if response.status_code != case.expected[role]:
                mismatches.append(
                    f"{case.method} {case.path} as {role}: "
                    f"expected {case.expected[role]}, got {response.status_code}"
                )
    assert mismatches == []


async def test_non_member_gets_404_not_403_for_existing_org(
    matrix_actors: Actors,
) -> None:
    workspace, browsers = matrix_actors
    response = await browsers["outsider"].get(f"/api/v1/orgs/{workspace.org_id}")
    assert response.status_code == 404
    unknown = await browsers["outsider"].get("/api/v1/orgs/00000000-0000-0000-0000-000000000000")
    assert unknown.json()["code"] == response.json()["code"] == "NOT_FOUND"


async def test_malformed_ids_are_404(matrix_actors: Actors) -> None:
    _, browsers = matrix_actors
    assert (await browsers["owner"].get("/api/v1/projects/not-a-uuid")).status_code == 404


async def test_unauthenticated_requests_get_401(client: Any) -> None:
    response = await client.get("/api/v1/orgs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
