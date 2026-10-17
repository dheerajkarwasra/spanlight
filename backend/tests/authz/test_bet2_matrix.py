"""The authorization matrix for the provider path routes (`rows_c.py`), on the harness of
`test_phase_2_to_5_matrix.py`: role outcomes from the permission table, 404 for a non-member,
401 without a credential, 403 `KEY_SCOPE` for a project API key, and a read-scoped personal
access token let in on every read and refused (403 `TOKEN_SCOPE`) on every write.
"""

import httpx
import pytest

from app.api import deps
from app.core.ratelimit import PostgresTokenBucket
from tests.authz.matrix import (
    WRITE_METHODS,
    Actors,
    Row,
    call,
    fill,
    problem_code,
    role_problem,
    send,
)
from tests.authz.rows_c import ROWS_C
from tests.helpers import bearer, create_token
from tests.key_readable import KEY_READABLE

ROWS = pytest.mark.parametrize("row", ROWS_C, ids=Row.label)
READ_ROWS = pytest.mark.parametrize(
    "row", [row for row in ROWS_C if row.method not in WRITE_METHODS], ids=Row.label
)
WRITE_ROWS = pytest.mark.parametrize(
    "row", [row for row in ROWS_C if row.method in WRITE_METHODS], ids=Row.label
)


def test_the_rows_cover_the_bet_2_routes() -> None:
    # A broken import must not pass by leaving the matrix empty.
    assert len(ROWS_C) >= 5
    assert len({row.route for row in ROWS_C}) == len(ROWS_C)
    assert not {row.route for row in ROWS_C} & KEY_READABLE


@ROWS
async def test_every_role_gets_what_the_permission_table_says(
    row: Row, matrix_actors: Actors
) -> None:
    """Outsider 404; a role without the permission 403; a role with it gets past the guard."""
    workspace, browsers = matrix_actors
    path = fill(row.path, workspace)
    problems = []
    for role, browser in browsers.items():
        status = (await send(browser, row.method, path)).status_code
        problem = role_problem(role, row, status)
        if problem:
            problems.append(f"{role}: got {status}, {problem}")
    assert problems == []


@ROWS
async def test_unauthenticated_requests_get_401(
    row: Row, client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    workspace, _ = matrix_actors
    response = await call(client, row.method, fill(row.path, workspace))
    assert (response.status_code, problem_code(response)) == (401, "UNAUTHORIZED")


@pytest.fixture
def unthrottled_key_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-credential read limit would answer 429 to a walk of every route."""
    monkeypatch.setattr(deps, "API_READ_LIMITER", PostgresTokenBucket(rate=1000, burst=10_000))


@ROWS
@pytest.mark.usefixtures("unthrottled_key_reads")
async def test_an_api_key_is_refused(
    row: Row, bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    workspace, _ = matrix_actors
    path = fill(row.path, workspace)
    response = await call(bare_client, row.method, path, bearer(workspace.api_key))
    assert (response.status_code, problem_code(response)) == (403, "KEY_SCOPE")


@READ_ROWS
@pytest.mark.usefixtures("unthrottled_key_reads")
async def test_a_read_token_of_a_member_reads(
    row: Row, bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    """A personal access token acts as its user: a viewer's read-scoped token is let in."""
    workspace, browsers = matrix_actors
    token = await create_token(browsers["viewer"], scope="read")
    path = fill(row.path, workspace)
    response = await call(bare_client, row.method, path, bearer(token["token"]))
    assert response.status_code == 200, response.text


@WRITE_ROWS
@pytest.mark.usefixtures("unthrottled_key_reads")
async def test_a_read_token_is_refused_on_a_write(
    row: Row, bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    """Even an owner's read-scoped token cannot vote: the permission is classed `write`."""
    workspace, browsers = matrix_actors
    token = await create_token(browsers["owner"], scope="read")
    path = fill(row.path, workspace)
    response = await call(bare_client, row.method, path, bearer(token["token"]))
    assert (response.status_code, problem_code(response)) == (403, "TOKEN_SCOPE")


@ROWS
@pytest.mark.usefixtures("unthrottled_key_reads")
async def test_a_token_of_a_non_member_gets_404(
    row: Row, bare_client: httpx.AsyncClient, matrix_actors: Actors
) -> None:
    workspace, browsers = matrix_actors
    token = await create_token(browsers["outsider"], scope="read")
    path = fill(row.path, workspace)
    response = await call(bare_client, row.method, path, bearer(token["token"]))
    assert response.status_code == 404, response.text
