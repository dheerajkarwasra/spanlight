"""Routes that need no sign-in do not serve a request that carries an API key.

A bearer request skips the Origin check, and on these routes that check is the only CSRF
defence. So a bearer is refused (403 `KEY_SCOPE` for a valid key, 401 otherwise) instead of being
served as if it were anonymous.
"""

from typing import Any

import httpx
import pytest

from tests.helpers import bearer, create_api_key, expire_api_key
from tests.keys.conftest import World

LOGIN = {"email": "owner@example.com", "password": "correct horse battery"}

ANONYMOUS_ROUTES = [
    pytest.param("POST", "/api/v1/auth/login", LOGIN, id="login"),
    pytest.param(
        "POST",
        "/api/v1/auth/signup",
        {"email": "new@example.com", "password": "correct horse battery", "name": "New"},
        id="signup",
    ),
    pytest.param("POST", "/api/v1/auth/email/verify/confirm", {"token": "x"}, id="verify"),
    pytest.param(
        "POST", "/api/v1/auth/password/forgot", {"email": "owner@example.com"}, id="forgot"
    ),
    pytest.param(
        "POST",
        "/api/v1/auth/password/reset",
        {"token": "x", "password": "correct horse battery"},
        id="reset",
    ),
    pytest.param(
        "POST", "/api/v1/auth/totp/verify", {"challenge": "x", "code": "123456"}, id="totp"
    ),
    pytest.param(
        "POST", "/api/v1/auth/email/change/confirm", {"token": "x"}, id="email-change-confirm"
    ),
    pytest.param("POST", "/api/v1/email/unsubscribe", {"token": "x"}, id="unsubscribe"),
    pytest.param("POST", "/api/v1/demo/session", None, id="demo"),
    pytest.param("GET", "/api/v1/auth/terms", None, id="terms"),
    pytest.param("GET", "/api/v1/auth/oauth/providers", None, id="oauth-providers"),
    pytest.param("GET", "/api/v1/auth/oauth/github/start", None, id="oauth-start"),
    pytest.param("GET", "/api/v1/auth/oauth/github/callback", None, id="oauth-callback"),
]


@pytest.mark.parametrize(("method", "path", "body"), ANONYMOUS_ROUTES)
@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_a_live_key_is_refused_on_an_anonymous_route(
    bare_client: httpx.AsyncClient,
    world: World,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    origin: str | None,
) -> None:
    for key in (world.read_key, world.workspace.api_key):
        headers = bearer(key)
        if origin is not None:
            headers["Origin"] = origin

        response = await bare_client.request(method, path, json=body, headers=headers)

        assert response.status_code == 403, (path, response.text)
        assert response.json()["code"] == "KEY_SCOPE"
        assert "set-cookie" not in response.headers
    assert len(bare_client.cookies) == 0


async def test_a_login_with_a_key_does_not_sign_anyone_in(
    bare_client: httpx.AsyncClient, world: World
) -> None:
    refused = await bare_client.post(
        "/api/v1/auth/login", json=LOGIN, headers=bearer(world.read_key)
    )
    assert refused.status_code == 403

    # No session exists for that attempt: the same request without the key still needs an Origin.
    assert (await bare_client.get("/api/v1/auth/me")).status_code == 401


async def test_a_signup_with_a_key_creates_no_account(
    bare_client: httpx.AsyncClient, client: httpx.AsyncClient, world: World
) -> None:
    body = {"email": "new@example.com", "password": "correct horse battery", "name": "New"}
    refused = await bare_client.post(
        "/api/v1/auth/signup", json=body, headers=bearer(world.read_key)
    )
    assert refused.status_code == 403

    # Had the account been created, this would be 409 EMAIL_TAKEN.
    assert (await client.post("/api/v1/auth/signup", json=body)).status_code == 201


@pytest.mark.parametrize("authorization", ["Bearer sk-not-ours", "Bearer", "bearer x"])
async def test_a_bearer_that_is_not_a_key_is_unauthorized_on_an_anonymous_route(
    bare_client: httpx.AsyncClient, world: World, authorization: str
) -> None:
    response = await bare_client.post(
        "/api/v1/auth/login", json=LOGIN, headers={"Authorization": authorization}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert "set-cookie" not in response.headers


async def test_a_revoked_or_expired_key_is_unauthorized_on_an_anonymous_route(
    bare_client: httpx.AsyncClient, world: World, session_factory: Any
) -> None:
    keys_url = f"/api/v1/projects/{world.workspace.project_id}/keys"
    revoked = await create_api_key(world.owner, world.workspace.project_id)
    assert (await world.owner.delete(f"{keys_url}/{revoked['id']}")).status_code == 204
    expired = await create_api_key(
        world.owner, world.workspace.project_id, expires_at="2099-01-01T00:00:00Z"
    )
    await expire_api_key(session_factory, expired["id"])

    for key, code in ((revoked["secret"], "UNAUTHORIZED"), (expired["secret"], "KEY_EXPIRED")):
        response = await bare_client.post("/api/v1/auth/login", json=LOGIN, headers=bearer(key))
        assert response.status_code == 401
        assert response.json()["code"] == code


async def test_anonymous_routes_are_unchanged_without_a_bearer(
    client: httpx.AsyncClient, world: World
) -> None:
    login = await client.post("/api/v1/auth/login", json=LOGIN)
    assert login.status_code == 200
    assert "spl_session" in login.headers["set-cookie"]
    assert (await client.get("/api/v1/auth/oauth/providers")).status_code == 200
