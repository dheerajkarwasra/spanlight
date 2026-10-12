"""Signup, login, sessions, logout."""

from typing import Any

import httpx

from tests.helpers import Browser

PASSWORD = "correct horse battery"


async def test_signup_sets_cookies_and_me_returns_user(browser_factory: Any) -> None:
    browser: Browser = await browser_factory("ada@example.com", "Ada")

    assert browser.http.cookies.get("spl_session")
    assert browser.http.cookies.get("spl_csrf")
    me = await browser.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "user": browser.user,
        "memberships": [],
        "has_password": True,
        "totp_enabled": False,
        # The test server sends no email, so no verification link could ever arrive.
        "email_verification_required": False,
    }
    assert set(browser.user) == {"id", "email", "name", "created_at", "email_verified"}


async def test_signup_rejects_duplicate_email_case_insensitively(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    await browser_factory("ada@example.com")
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "ADA@example.com", "password": PASSWORD, "name": "Imposter"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_TAKEN"


async def test_signup_validates_password_length(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/signup", json={"email": "bob@example.com", "password": "short", "name": "B"}
    )
    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "password"
    assert body["request_id"]


async def test_login_and_logout(browser_factory: Any, client: httpx.AsyncClient) -> None:
    await browser_factory("ada@example.com")

    login = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["status"] == "signed_in"
    assert login.json()["user"]["email"] == "ada@example.com"

    csrf = client.cookies["spl_csrf"]
    logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_wrong_password_is_generic(browser_factory: Any, client: httpx.AsyncClient) -> None:
    await browser_factory("ada@example.com")
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "nope nope nope"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "nope nope nope"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


async def test_sessions_list_marks_current_and_can_revoke_other(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    browser: Browser = await browser_factory("ada@example.com")
    await client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})

    sessions = (await browser.get("/api/v1/auth/sessions")).json()
    assert len(sessions) == 2
    other = next(session for session in sessions if not session["current"])

    assert (await browser.delete(f"/api/v1/auth/sessions/{other['id']}")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await browser.get("/api/v1/auth/me")).status_code == 200


async def test_cannot_revoke_someone_elses_session(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    bob: Browser = await browser_factory("bob@example.com")
    bob_session = (await bob.get("/api/v1/auth/sessions")).json()[0]["id"]

    assert (await ada.delete(f"/api/v1/auth/sessions/{bob_session}")).status_code == 404
    assert (await bob.get("/api/v1/auth/me")).status_code == 200
