"""The authorization matrix for the Phase 2-5 routes (every row of `rows_a.py` and `rows_b.py`):
gateway, alerts, budgets, insights, releases, users, exports, prompts, scores, datasets,
annotation queues, evaluators, experiments and the playground, plus the organization management
routes `test_authz.py` leaves out. How the checks stay safe to repeat is in
`tests/authz/matrix.py`.
"""

import httpx
import pytest

from app.api import deps
from app.core.ratelimit import PostgresTokenBucket
from tests.authz.matrix import Actors, call, fill, problem_code, role_mismatches
from tests.authz.rows import ALL_ROWS
from tests.helpers import bearer
from tests.key_readable import KEY_READABLE

BEARER_NOT_A_KEY = "Bearer sk-not-ours"


def test_the_rows_cover_the_phase_2_to_5_domains() -> None:
    # A broken import must not pass by leaving the matrix empty.
    assert len(ALL_ROWS) >= 125
    assert len({row.route for row in ALL_ROWS}) == len(ALL_ROWS)


async def test_every_role_gets_what_the_permission_table_says(matrix_actors: Actors) -> None:
    """Outsider 404; a role without the permission 403; a role with it gets past the guard."""
    workspace, browsers = matrix_actors
    assert await role_mismatches(ALL_ROWS, workspace, browsers) == []


async def test_unauthenticated_requests_get_401(
    client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    workspace, _ = matrix_actors
    mismatches = []
    for row in ALL_ROWS:
        response = await call(client, row.method, fill(row.path, workspace))
        code = problem_code(response)
        if (response.status_code, code) != (401, "UNAUTHORIZED"):
            mismatches.append(f"{row.label()}: {response.status_code} {code}")
    assert mismatches == []


@pytest.fixture
def unthrottled_key_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-credential read limit (bursts of 40) would answer 429 to a walk of every route."""
    monkeypatch.setattr(deps, "API_READ_LIMITER", PostgresTokenBucket(rate=1000, burst=10_000))


@pytest.mark.usefixtures("unthrottled_key_reads")
async def test_an_api_key_is_refused_on_every_route_that_is_not_key_readable(
    bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    """A live project key gets 403 `KEY_SCOPE` (the key inventory pins the readable routes)."""
    workspace, _ = matrix_actors
    mismatches = []
    for row in ALL_ROWS:
        if row.route in KEY_READABLE:
            continue
        path = fill(row.path, workspace)
        response = await call(bare_client, row.method, path, bearer(workspace.api_key))
        code = problem_code(response)
        if (response.status_code, code) != (403, "KEY_SCOPE"):
            mismatches.append(f"{row.label()}: {response.status_code} {code}")
    assert mismatches == []


async def test_a_bearer_that_is_not_a_key_gets_401(
    bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    workspace, _ = matrix_actors
    mismatches = []
    for row in ALL_ROWS:
        headers = {"Authorization": BEARER_NOT_A_KEY}
        response = await call(bare_client, row.method, fill(row.path, workspace), headers)
        code = problem_code(response)
        if (response.status_code, code) != (401, "UNAUTHORIZED"):
            mismatches.append(f"{row.label()}: {response.status_code} {code}")
    assert mismatches == []


async def test_a_refused_write_changes_nothing(matrix_actors: Actors) -> None:
    """A viewer's refused price override leaves the organization exactly as it was."""
    workspace, browsers = matrix_actors
    path = fill("/api/v1/orgs/{org_id}/price-overrides", workspace)
    body = {
        "provider": "openai",
        "model_pattern": "gpt-existing",
        "input_per_mtok": "1",
        "output_per_mtok": "2",
    }
    created = await browsers["owner"].post(path, json=body)
    assert created.status_code == 201, created.text
    listed = await browsers["owner"].get(path)
    assert listed.status_code == 200, listed.text
    before = listed.json()
    assert len(before) == 1

    refused = await browsers["viewer"].post(path, json={**body, "model_pattern": "gpt-sneaky"})

    assert refused.status_code == 403
    after = await browsers["owner"].get(path)
    assert after.status_code == 200, after.text
    assert after.json() == before
