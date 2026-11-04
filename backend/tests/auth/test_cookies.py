"""Session and CSRF cookie names: `__Host-` prefixed when cookies are `Secure`."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app

HTTPS_ORIGIN = "https://testserver"
SIGNUP = {"email": "ada@example.com", "password": "correct horse battery", "name": "Ada"}


@pytest.fixture
async def https_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    https = settings.model_copy(update={"app_base_url": HTTPS_ORIGIN})
    application: Any = create_app(https)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url=HTTPS_ORIGIN,
            headers={"Origin": HTTPS_ORIGIN},
        ) as http,
    ):
        yield http


def _set_cookies(response: httpx.Response) -> dict[str, str]:
    headers = response.headers.get_list("set-cookie")
    return {header.partition("=")[0]: header for header in headers}


async def test_secure_cookies_use_the_host_prefix(https_client: httpx.AsyncClient) -> None:
    response = await https_client.post("/api/v1/auth/signup", json=SIGNUP)

    assert response.status_code == 201, response.text
    cookies = _set_cookies(response)
    assert set(cookies) == {"__Host-spl_session", "__Host-spl_csrf"}
    for header in cookies.values():
        attributes = [part.strip().lower() for part in header.split(";")]
        assert "secure" in attributes
        assert "path=/" in attributes
        assert not any(part.startswith("domain=") for part in attributes)

    me = await https_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text


async def test_the_unprefixed_session_cookie_is_ignored_over_https(
    https_client: httpx.AsyncClient,
) -> None:
    signup = await https_client.post("/api/v1/auth/signup", json=SIGNUP)
    assert signup.status_code == 201, signup.text
    token = https_client.cookies["__Host-spl_session"]
    https_client.cookies.clear()

    # A sibling subdomain can plant `spl_session`, but it cannot plant a `__Host-` cookie.
    https_client.cookies.set("spl_session", token)
    me = await https_client.get("/api/v1/auth/me")

    assert me.status_code == 401


async def test_plain_cookie_names_without_https(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/signup", json=SIGNUP)

    assert response.status_code == 201, response.text
    assert set(_set_cookies(response)) == {"spl_session", "spl_csrf"}


async def test_the_csrf_check_reads_the_prefixed_cookie(https_client: httpx.AsyncClient) -> None:
    signup = await https_client.post("/api/v1/auth/signup", json=SIGNUP)
    assert signup.status_code == 201, signup.text
    csrf = https_client.cookies["__Host-spl_csrf"]

    refused = await https_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "x" + csrf})
    logout = await https_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    assert refused.status_code == 403
    assert logout.status_code == 204, logout.text
