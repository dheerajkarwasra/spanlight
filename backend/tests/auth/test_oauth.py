"""Sign in with GitHub and Google: start, callback, linking, unlinking and the refusals.

Provider traffic goes through `FakeProviders` on an `httpx.MockTransport`; nothing reaches the
network. The browser side is a plain `httpx.AsyncClient`, which keeps its cookies between
calls and does not follow redirects, so each test reads the `Location` the app answered with.
"""

import asyncio
import base64
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import auth as auth_routes
from app.api.v1 import oauth as oauth_routes
from app.auth.email_tokens import issue_token
from app.auth.oauth_state import Intent, OAuthState, sign_state
from app.config import Settings
from app.db.models import (
    AuditEvent,
    EmailTokenKind,
    LoginAttempt,
    OAuthIdentity,
    User,
)
from tests.auth.conftest import GITHUB_FIELDS, running_app
from tests.auth.oauth_fakes import (
    GITHUB_EMAILS_URL,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    FakeProviders,
)
from tests.conftest import TEST_ORIGIN, BrowserFactory, _make_client
from tests.helpers import Browser

SessionFactory = async_sessionmaker[AsyncSession]

PREFIX = "/api/v1/auth/oauth"
ME = "/api/v1/auth/me"
LOGIN = "/api/v1/auth/login"


# --- helpers ----------------------------------------------------------------------------------


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def _start(client: httpx.AsyncClient, provider: str = "github", **params: str) -> Any:
    return await client.get(f"{PREFIX}/{provider}/start", params=params)


async def _callback(client: httpx.AsyncClient, provider: str = "github", **params: str) -> Any:
    return await client.get(f"{PREFIX}/{provider}/callback", params=params)


async def _sign_in(
    client: httpx.AsyncClient,
    provider: str = "github",
    *,
    next: str | None = None,
    intent: str = "sign_in",
    code: str = "the-code",
) -> httpx.Response:
    """The whole round trip: start, "the user approves at the provider", callback."""
    params = {"intent": intent}
    if next is not None:
        params["next"] = next
    started = await _start(client, provider, **params)
    assert started.status_code == 302, started.text
    state = _query(started.headers["location"])["state"]
    return await _callback(client, provider, code=code, state=state)


def _went_to(response: httpx.Response, path: str) -> None:
    assert response.status_code == 302, response.text
    assert response.headers["location"] == f"{TEST_ORIGIN}{path}"


def _has_session_cookies(client: httpx.AsyncClient) -> bool:
    return bool(client.cookies.get("spl_session")) and bool(client.cookies.get("spl_csrf"))


def _set_cookie(response: httpx.Response, name: str) -> str:
    matches = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")]
    assert len(matches) == 1, response.headers.get_list("set-cookie")
    return matches[0]


def _state_cookie_value(response: httpx.Response) -> str:
    return _set_cookie(response, "spl_oauth").split(";", 1)[0].split("=", 1)[1]


async def _users(factory: SessionFactory) -> list[User]:
    async with factory() as db:
        return list((await db.scalars(select(User).order_by(User.created_at))).all())


async def _identities(factory: SessionFactory) -> list[OAuthIdentity]:
    async with factory() as db:
        return list(
            (await db.scalars(select(OAuthIdentity).order_by(OAuthIdentity.created_at))).all()
        )


async def _audit_actions(factory: SessionFactory) -> list[str]:
    async with factory() as db:
        return list(
            (await db.scalars(select(AuditEvent.action).order_by(AuditEvent.created_at))).all()
        )


async def _verify_email(factory: SessionFactory, email: str) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE users SET email_verified_at = now() WHERE email = :email"),
            {"email": email},
        )
        await db.commit()


async def _oauth_only_browser(
    client: httpx.AsyncClient, providers: FakeProviders, email: str, *, subject: int = 1001
) -> Browser:
    """Sign a new, password-less user up with GitHub and return it as a signed-in Browser."""
    providers.github_user = {**providers.github_user, "id": subject}
    providers.set_github_email(email)
    response = await _sign_in(client)
    _went_to(response, "/")
    me = await client.get(ME)
    assert me.status_code == 200, me.text
    return Browser(http=client, user=me.json()["user"])


# --- start -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "authorize_url", "scope"),
    [
        ("github", "https://github.com/login/oauth/authorize", "read:user user:email"),
        ("google", "https://accounts.google.com/o/oauth2/v2/auth", "openid email profile"),
    ],
)
async def test_start_redirects_to_the_provider_with_pkce(
    oauth_client: httpx.AsyncClient, provider: str, authorize_url: str, scope: str
) -> None:
    response = await _start(oauth_client, provider)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{authorize_url}?")
    params = _query(location)
    assert params["client_id"] == f"{provider}-client-id"
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == f"{TEST_ORIGIN}{PREFIX}/{provider}/callback"
    assert params["scope"] == scope
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"]
    assert len(params["state"]) >= 32
    assert response.headers["cache-control"] == "no-store"


async def test_start_stores_the_state_in_a_signed_http_only_cookie(
    oauth_client: httpx.AsyncClient,
) -> None:
    response = await _start(oauth_client)

    cookie = _set_cookie(response, "spl_oauth").lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/api/v1/auth/oauth" in cookie
    assert "max-age=600" in cookie
    assert "secure" not in cookie.split("; ")  # APP_BASE_URL is http in tests


async def test_state_cookie_is_secure_when_the_base_url_is_https(
    settings: Settings, providers: FakeProviders
) -> None:
    https = settings.model_copy(
        update={**GITHUB_FIELDS, "app_base_url": "https://spanlight.example"}
    )
    async with running_app(https, providers) as application, _make_client(application) as client:
        response = await _start(client)

    assert "secure" in _set_cookie(response, "spl_oauth").lower().split("; ")


async def test_every_start_gets_its_own_state(oauth_client: httpx.AsyncClient) -> None:
    first = _query((await _start(oauth_client)).headers["location"])
    second = _query((await _start(oauth_client)).headers["location"])

    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


async def test_start_for_an_unconfigured_provider_is_404(client: httpx.AsyncClient) -> None:
    response = await _start(client, "github")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert "spl_oauth" not in response.headers.get("set-cookie", "")


async def test_only_configured_providers_can_start(github_only_client: httpx.AsyncClient) -> None:
    assert (await _start(github_only_client, "github")).status_code == 302
    assert (await _start(github_only_client, "google")).status_code == 404
    assert (await _callback(github_only_client, "google", code="c", state="s")).status_code == 404


async def test_an_unknown_provider_is_404(oauth_client: httpx.AsyncClient) -> None:
    assert (await _start(oauth_client, "gitlab")).status_code == 404
    assert (await _callback(oauth_client, "gitlab", code="c", state="s")).status_code == 404


async def test_rejects_an_unknown_intent(oauth_client: httpx.AsyncClient) -> None:
    response = await _start(oauth_client, intent="delete_everything")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_link_needs_a_session(oauth_client: httpx.AsyncClient) -> None:
    response = await _start(oauth_client, intent="link")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# --- providers ---------------------------------------------------------------------------------


async def test_providers_lists_only_configured_ones(
    client: httpx.AsyncClient,
    github_only_client: httpx.AsyncClient,
    oauth_client: httpx.AsyncClient,
) -> None:
    none = await client.get(f"{PREFIX}/providers")
    one = await github_only_client.get(f"{PREFIX}/providers")
    both = await oauth_client.get(f"{PREFIX}/providers")

    assert none.json() == []
    assert one.json() == [{"provider": "github"}]
    assert both.json() == [{"provider": "github"}, {"provider": "google"}]


# --- sign-in: new and returning users ----------------------------------------------------------


async def test_first_sign_in_creates_a_verified_user_without_a_password(
    oauth_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    response = await _sign_in(oauth_client, next="/dashboard")

    _went_to(response, "/dashboard")
    assert _has_session_cookies(oauth_client)
    assert response.headers["cache-control"] == "no-store"
    me = (await oauth_client.get(ME)).json()
    assert me["user"]["email"] == "ada@example.com"
    assert me["user"]["name"] == "Ada Lovelace"
    assert me["user"]["email_verified"] is True
    assert me["has_password"] is False

    (user,) = await _users(session_factory)
    assert user.password_hash is None
    assert user.email_verified_at is not None
    (identity,) = await _identities(session_factory)
    assert (identity.user_id, identity.provider, identity.subject, identity.email) == (
        user.id,
        "github",
        "1001",
        "ada@example.com",
    )


async def test_sign_in_with_google_creates_a_user_too(
    oauth_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    response = await _sign_in(oauth_client, "google")

    _went_to(response, "/")
    assert _has_session_cookies(oauth_client)
    (identity,) = await _identities(session_factory)
    assert (identity.provider, identity.subject) == ("google", "google-2002")


async def test_the_default_destination_is_the_root(oauth_client: httpx.AsyncClient) -> None:
    _went_to(await _sign_in(oauth_client), "/")


async def test_the_state_cookie_is_cleared_by_the_callback(
    oauth_client: httpx.AsyncClient,
) -> None:
    response = await _sign_in(oauth_client)

    cleared = _set_cookie(response, "spl_oauth").lower()
    assert "max-age=0" in cleared
    assert "path=/api/v1/auth/oauth" in cleared
    assert oauth_client.cookies.get("spl_oauth") is None


async def test_second_sign_in_resolves_by_provider_subject(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    await _sign_in(oauth_client)
    (first,) = await _identities(session_factory)
    oauth_client.cookies.clear()

    # The address changed and is unverified now; the stable subject is what identifies the user.
    providers.set_github_email("renamed@example.com", verified=False)
    response = await _sign_in(oauth_client)

    _went_to(response, "/")
    assert _has_session_cookies(oauth_client)
    assert len(await _users(session_factory)) == 1
    (second,) = await _identities(session_factory)
    assert second.id == first.id
    assert second.last_used_at > first.last_used_at


async def test_a_replayed_callback_is_refused(oauth_client: httpx.AsyncClient) -> None:
    started = await _start(oauth_client)
    state = _query(started.headers["location"])["state"]
    assert (await _callback(oauth_client, code="c", state=state)).status_code == 302
    oauth_client.cookies.clear()

    replay = await _callback(oauth_client, code="c", state=state)

    _went_to(replay, "/login?error=OAUTH_STATE")
    assert not _has_session_cookies(oauth_client)


# --- sign-in: matching an existing account -----------------------------------------------------


async def test_verified_email_links_the_existing_verified_account(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    local = await oauth_browser_factory("ada@example.com")
    org = await local.post("/api/v1/orgs", json={"name": "Acme"})
    assert org.status_code == 201, org.text
    await _verify_email(session_factory, "ada@example.com")

    response = await _sign_in(oauth_client)

    _went_to(response, "/")
    assert _has_session_cookies(oauth_client)
    (user,) = await _users(session_factory)
    assert user.password_hash is not None  # the password keeps working
    (identity,) = await _identities(session_factory)
    assert identity.user_id == user.id
    me = (await oauth_client.get(ME)).json()
    assert me["user"]["id"] == str(user.id)
    assert me["has_password"] is True
    async with session_factory() as db:
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "user.oauth_link"))
        ).all()
    assert len(events) == 1
    event = events[0]
    assert str(event.org_id) == org.json()["id"]
    assert event.actor_user_id == user.id
    assert (event.target_type, event.target_id) == ("user", str(user.id))
    assert event.metadata_ == {"provider": "github", "via": "email_match"}


async def test_audits_the_link_in_each_org_of_the_user(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    local = await oauth_browser_factory("ada@example.com")
    for name in ("Acme", "Globex"):
        assert (await local.post("/api/v1/orgs", json={"name": name})).status_code == 201
    await _verify_email(session_factory, "ada@example.com")

    await _sign_in(oauth_client)

    async with session_factory() as db:
        orgs = (
            await db.scalars(
                select(AuditEvent.org_id).where(AuditEvent.action == "user.oauth_link")
            )
        ).all()
    assert len(set(orgs)) == 2


async def test_unverified_provider_email_does_not_link(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    await oauth_browser_factory("ada@example.com")
    await _verify_email(session_factory, "ada@example.com")
    providers.set_github_email("ada@example.com", verified=False)

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=EMAIL_UNVERIFIED_AT_PROVIDER")
    assert not _has_session_cookies(oauth_client)
    assert await _identities(session_factory) == []
    assert "user.oauth_link" not in await _audit_actions(session_factory)


async def test_unverified_local_account_does_not_link(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    # Whoever registered the address first never proved it is theirs, so a provider that did
    # prove it must not be handed their account.
    await oauth_browser_factory("ada@example.com")

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=ACCOUNT_EMAIL_UNVERIFIED")
    assert not _has_session_cookies(oauth_client)
    assert await _identities(session_factory) == []
    (user,) = await _users(session_factory)
    assert user.email_verified_at is None
    assert "user.oauth_link" not in await _audit_actions(session_factory)


async def test_unverified_provider_email_creates_no_account(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    providers.set_github_email("victim@example.com", verified=False)

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=EMAIL_UNVERIFIED_AT_PROVIDER")
    assert not _has_session_cookies(oauth_client)
    assert await _users(session_factory) == []
    assert await _identities(session_factory) == []


async def test_a_provider_without_an_email_creates_no_account(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    providers.github_emails = []

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=EMAIL_UNVERIFIED_AT_PROVIDER")
    assert await _users(session_factory) == []


async def test_a_matching_email_does_not_replace_a_different_identity_of_the_same_provider(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    await oauth_browser_factory("ada@example.com")
    await _verify_email(session_factory, "ada@example.com")
    await _sign_in(oauth_client)  # links GitHub account 1001
    oauth_client.cookies.clear()

    # Another GitHub account with the same verified address must not become a second sign-in.
    providers.github_user = {**providers.github_user, "id": 1002}
    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=OAUTH_ALREADY_LINKED")
    assert not _has_session_cookies(oauth_client)
    (identity,) = await _identities(session_factory)
    assert identity.subject == "1001"


# --- linking and unlinking ---------------------------------------------------------------------


async def test_link_attaches_the_identity_to_the_signed_in_user(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    org = await browser.post("/api/v1/orgs", json={"name": "Acme"})
    assert org.status_code == 201, org.text

    # The GitHub address differs from the account's: linking is the signed-in user's own choice.
    response = await _sign_in(browser.http, intent="link")

    _went_to(response, "/settings/security")
    (user,) = await _users(session_factory)
    (identity,) = await _identities(session_factory)
    assert identity.user_id == user.id
    assert identity.email == "ada@example.com"
    assert "user.oauth_link" in await _audit_actions(session_factory)
    listed = await browser.get(f"{PREFIX}/identities")
    assert [row["provider"] for row in listed.json()] == ["github"]


async def test_link_goes_back_to_next(verified_browser_factory: BrowserFactory) -> None:
    browser = await verified_browser_factory("ada@example.com")

    response = await _sign_in(browser.http, intent="link", next="/settings/profile")

    _went_to(response, "/settings/profile")


async def test_linking_does_not_create_a_second_session(
    verified_browser_factory: BrowserFactory,
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    before = browser.http.cookies.get("spl_session")

    await _sign_in(browser.http, intent="link")

    assert browser.http.cookies.get("spl_session") == before


async def test_an_identity_linked_to_another_user_is_refused(
    oauth_client: httpx.AsyncClient,
    verified_browser_factory: BrowserFactory,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    other = await verified_browser_factory("grace@example.com")

    response = await _sign_in(other.http, intent="link")

    _went_to(response, "/settings/security?error=OAUTH_ALREADY_LINKED")
    (identity,) = await _identities(session_factory)
    assert str(identity.user_id) != other.user["id"]


async def test_a_second_account_of_the_same_provider_is_refused(
    verified_browser_factory: BrowserFactory, providers: FakeProviders
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    assert (await _sign_in(browser.http, intent="link")).status_code == 302
    providers.github_user = {**providers.github_user, "id": 1002}

    response = await _sign_in(browser.http, intent="link")

    _went_to(response, "/settings/security?error=OAUTH_ALREADY_LINKED")


async def test_linking_the_same_account_again_is_fine(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    await _sign_in(browser.http, intent="link")

    response = await _sign_in(browser.http, intent="link")

    _went_to(response, "/settings/security")
    assert len(await _identities(session_factory)) == 1


async def test_link_errors_return_to_next_with_the_error_appended(
    verified_browser_factory: BrowserFactory, providers: FakeProviders
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    providers.overrides[GITHUB_TOKEN_URL] = lambda: httpx.Response(500)

    response = await _sign_in(browser.http, intent="link", next="/settings/security?tab=sso#x")

    _went_to(response, "/settings/security?tab=sso&error=OAUTH_PROVIDER_ERROR#x")


async def test_link_by_another_session_user_is_refused(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await verified_browser_factory("ada@example.com")
    grace = await verified_browser_factory("grace@example.com")
    started = await _start(ada.http, intent="link")
    state = _query(started.headers["location"])["state"]
    # Grace's browser ends up carrying the state Ada started (the cookie is all the state there
    # is), so the callback arrives in a session that is not the one that began the link.
    grace.http.cookies.set(
        "spl_oauth", _state_cookie_value(started), domain="testserver.local", path=PREFIX
    )

    response = await _callback(grace.http, code="c", state=state)

    _went_to(response, "/settings/security?error=OAUTH_STATE")
    assert await _identities(session_factory) == []


async def test_link_without_a_session_at_the_callback_is_refused(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    started = await _start(browser.http, intent="link")
    state = _query(started.headers["location"])["state"]
    browser.http.cookies.delete("spl_session")
    browser.http.cookies.delete("spl_csrf")

    response = await _callback(browser.http, code="c", state=state)

    _went_to(response, "/settings/security?error=OAUTH_STATE")
    assert await _identities(session_factory) == []


async def test_identities_lists_the_linked_providers(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    browser = await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    assert (await _sign_in(oauth_client, "google", intent="link")).status_code == 302

    response = await browser.get(f"{PREFIX}/identities")

    assert response.status_code == 200
    rows = response.json()
    assert [row["provider"] for row in rows] == ["github", "google"]
    assert all(row["email"] == "ada@example.com" for row in rows)
    assert all(row["created_at"] and row["last_used_at"] for row in rows)
    assert all(set(row) == {"provider", "email", "created_at", "last_used_at"} for row in rows)


async def test_identities_needs_a_session(oauth_client: httpx.AsyncClient) -> None:
    assert (await oauth_client.get(f"{PREFIX}/identities")).status_code == 401


async def test_identities_shows_only_the_callers_own(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    providers: FakeProviders,
) -> None:
    await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    other = await oauth_browser_factory("grace@example.com")

    assert (await other.get(f"{PREFIX}/identities")).json() == []


async def test_unlink_removes_the_identity_and_audits_it(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    assert (await browser.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201
    await _sign_in(browser.http, intent="link")

    response = await browser.delete(f"{PREFIX}/github")

    assert response.status_code == 204
    assert await _identities(session_factory) == []
    assert "user.oauth_unlink" in await _audit_actions(session_factory)


async def test_unlink_keeps_the_only_way_to_sign_in(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    browser = await _oauth_only_browser(oauth_client, providers, "ada@example.com")

    response = await browser.delete(f"{PREFIX}/github")

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_SIGN_IN_METHOD"
    assert len(await _identities(session_factory)) == 1


async def test_unlink_is_allowed_when_another_identity_remains(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    browser = await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    assert (await _sign_in(oauth_client, "google", intent="link")).status_code == 302

    response = await browser.delete(f"{PREFIX}/github")

    assert response.status_code == 204
    (remaining,) = await _identities(session_factory)
    assert remaining.provider == "google"


async def test_two_concurrent_unlinks_cannot_remove_every_sign_in_method(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    browser = await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    assert (await _sign_in(oauth_client, "google", intent="link")).status_code == 302

    first, second = await asyncio.gather(
        browser.delete(f"{PREFIX}/github"), browser.delete(f"{PREFIX}/google")
    )

    assert sorted([first.status_code, second.status_code]) == [204, 409]
    assert len(await _identities(session_factory)) == 1


async def test_unlink_when_not_linked_is_404(oauth_browser_factory: BrowserFactory) -> None:
    browser = await oauth_browser_factory("ada@example.com")

    response = await browser.delete(f"{PREFIX}/github")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_unlink_of_an_unknown_provider_is_404(oauth_browser_factory: BrowserFactory) -> None:
    browser = await oauth_browser_factory("ada@example.com")

    assert (await browser.delete(f"{PREFIX}/gitlab")).status_code == 404


async def test_unlink_needs_a_session_and_csrf(
    oauth_client: httpx.AsyncClient, verified_browser_factory: BrowserFactory
) -> None:
    assert (await oauth_client.delete(f"{PREFIX}/github")).status_code == 401
    browser = await verified_browser_factory("ada@example.com")
    await _sign_in(browser.http, intent="link")

    without_csrf = await browser.http.delete(f"{PREFIX}/github")

    assert without_csrf.status_code == 403
    assert without_csrf.json()["code"] == "CSRF_FAILED"


async def test_unlink_works_after_the_provider_was_removed_from_the_settings(
    settings: Settings,
    providers: FakeProviders,
    verified_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    await _sign_in(browser.http, intent="link")
    # An app with no OAuth settings at all, sharing the browser's session.
    async with running_app(settings, providers) as bare, _make_client(bare) as other:
        other.cookies.update(browser.http.cookies)
        headers = {"X-CSRF-Token": browser.http.cookies["spl_csrf"]}
        response = await other.delete(f"{PREFIX}/github", headers=headers)

    assert response.status_code == 204
    assert await _identities(session_factory) == []


# --- passwords ---------------------------------------------------------------------------------


async def test_password_login_for_a_user_without_a_password_is_refused(
    oauth_client: httpx.AsyncClient,
    oauth_app: Any,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    async with _make_client(oauth_app) as anonymous:
        known = await anonymous.post(LOGIN, json={"email": "ada@example.com", "password": "x" * 12})
        unknown = await anonymous.post(
            LOGIN, json={"email": "nobody@example.com", "password": "x" * 12}
        )

    assert known.status_code == 401
    assert known.json()["code"] == "INVALID_CREDENTIALS"
    assert known.json()["detail"] == unknown.json()["detail"]
    assert "spl_session" not in known.headers.get("set-cookie", "")
    async with session_factory() as db:
        failures = await db.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))
        )
    assert failures == 2


async def test_the_demo_account_reports_no_password(oauth_client: httpx.AsyncClient) -> None:
    started = await oauth_client.post("/api/v1/demo/session")
    assert started.status_code == 200, started.text

    assert (await oauth_client.get(ME)).json()["has_password"] is False


async def test_password_login_for_a_user_without_a_password_still_hashes_something(
    oauth_client: httpx.AsyncClient,
    oauth_app: Any,
    providers: FakeProviders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `verify_password_async(None, …)` verifies a dummy hash, so the answer takes as long as one
    # for a real account and does not tell a caller that this address has no password.
    await _oauth_only_browser(oauth_client, providers, "ada@example.com")
    seen: list[str | None] = []
    real = auth_routes.verify_password_async

    async def spy(password_hash: str | None, password: str) -> bool:
        seen.append(password_hash)
        return await real(password_hash, password)

    monkeypatch.setattr(auth_routes, "verify_password_async", spy)
    async with _make_client(oauth_app) as anonymous:
        await anonymous.post(LOGIN, json={"email": "ada@example.com", "password": "x" * 12})

    assert seen == [None]


# --- state and CSRF ----------------------------------------------------------------------------


def _forged_cookie(
    settings: Settings,
    *,
    state: str,
    provider: str = "github",
    lifetime: timedelta = timedelta(minutes=5),
    secret: str | None = None,
    intent: Intent = "sign_in",
    user_id: uuid.UUID | None = None,
    next: str = "/",
) -> str:
    return sign_state(
        secret or settings.secret_key.get_secret_value(),
        OAuthState(
            state=state,
            verifier="v" * 43,
            provider=provider,
            intent=intent,
            next=next,
            user_id=user_id,
            exp=datetime.now(UTC) + lifetime,
        ),
    )


async def _callback_with_cookie(
    app: Any, cookie: str | None, *, state: str | None = "the-state"
) -> httpx.Response:
    params = {"code": "c"}
    if state is not None:
        params["state"] = state
    headers = {"Cookie": f"spl_oauth={cookie}"} if cookie else {}
    async with _make_client(app) as browser:
        response = await browser.get(f"{PREFIX}/github/callback", params=params, headers=headers)
    assert not _has_session_cookies(browser)
    return response


async def test_a_callback_without_the_state_cookie_is_refused(
    oauth_app: Any, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    response = await _callback_with_cookie(oauth_app, None)

    _went_to(response, "/login?error=OAUTH_STATE")
    assert providers.requests == []  # the code was never exchanged
    assert await _users(session_factory) == []


async def test_a_callback_with_the_wrong_state_is_refused(
    oauth_app: Any, oauth_settings: Settings, providers: FakeProviders
) -> None:
    cookie = _forged_cookie(oauth_settings, state="the-state")

    response = await _callback_with_cookie(oauth_app, cookie, state="another-state")

    _went_to(response, "/login?error=OAUTH_STATE")
    assert providers.requests == []


async def test_a_callback_without_a_state_parameter_is_refused(
    oauth_app: Any, oauth_settings: Settings
) -> None:
    cookie = _forged_cookie(oauth_settings, state="the-state")

    _went_to(await _callback_with_cookie(oauth_app, cookie, state=None), "/login?error=OAUTH_STATE")


async def test_a_callback_with_a_tampered_cookie_is_refused(
    oauth_app: Any, oauth_settings: Settings, providers: FakeProviders
) -> None:
    payload, signature = _forged_cookie(oauth_settings, state="the-state").split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]

    response = await _callback_with_cookie(oauth_app, f"{payload}.{flipped}")

    _went_to(response, "/login?error=OAUTH_STATE")
    assert providers.requests == []


async def test_a_callback_with_a_cookie_signed_by_another_key_is_refused(
    oauth_app: Any, oauth_settings: Settings
) -> None:
    cookie = _forged_cookie(oauth_settings, state="the-state", secret="not-the-secret-key")

    _went_to(await _callback_with_cookie(oauth_app, cookie), "/login?error=OAUTH_STATE")


async def test_a_callback_with_an_expired_cookie_is_refused(
    oauth_app: Any, oauth_settings: Settings, providers: FakeProviders
) -> None:
    cookie = _forged_cookie(oauth_settings, state="the-state", lifetime=timedelta(seconds=-1))

    response = await _callback_with_cookie(oauth_app, cookie)

    _went_to(response, "/login?error=OAUTH_STATE")
    assert providers.requests == []


async def test_a_state_started_for_one_provider_is_refused_at_another(
    oauth_client: httpx.AsyncClient,
) -> None:
    started = await _start(oauth_client, "github")
    state = _query(started.headers["location"])["state"]

    response = await _callback(oauth_client, "google", code="c", state=state)

    _went_to(response, "/login?error=OAUTH_STATE")


async def test_a_link_callback_with_the_wrong_state_returns_to_next(
    verified_browser_factory: BrowserFactory,
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    await _start(browser.http, intent="link", next="/settings/security")

    response = await _callback(browser.http, code="c", state="forged")

    _went_to(response, "/settings/security?error=OAUTH_STATE")


async def test_the_state_cookie_is_cleared_when_the_callback_fails(
    oauth_client: httpx.AsyncClient,
) -> None:
    await _start(oauth_client)

    response = await _callback(oauth_client, code="c", state="forged")

    assert "max-age=0" in _set_cookie(response, "spl_oauth").lower()
    assert oauth_client.cookies.get("spl_oauth") is None


async def test_a_sign_in_whose_user_has_gone_by_the_time_it_is_locked_is_sent_back_to_start(
    oauth_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nobody(_db: AsyncSession, _user_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(oauth_routes, "lock_user_for_sign_in", nobody)

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=OAUTH_STATE")
    assert not _has_session_cookies(oauth_client)
    assert await _users(session_factory) == []  # what the callback wrote was rolled back


# --- provider failures -------------------------------------------------------------------------


def _connect_error() -> NoReturn:
    raise httpx.ConnectError("provider unreachable")


def _timeout() -> NoReturn:
    raise httpx.ReadTimeout("provider too slow")


PROVIDER_FAILURES: dict[str, dict[str, Callable[[], httpx.Response]]] = {
    "token error body": {
        GITHUB_TOKEN_URL: lambda: httpx.Response(200, json={"error": "bad_verification_code"})
    },
    "token 400": {GITHUB_TOKEN_URL: lambda: httpx.Response(400, json={"error": "invalid_grant"})},
    "token 500": {GITHUB_TOKEN_URL: lambda: httpx.Response(500)},
    "token not json": {GITHUB_TOKEN_URL: lambda: httpx.Response(200, text="not json")},
    "token without access_token": {GITHUB_TOKEN_URL: lambda: httpx.Response(200, json={})},
    "token unreachable": {GITHUB_TOKEN_URL: _connect_error},
    "token timeout": {GITHUB_TOKEN_URL: _timeout},
    "profile 500": {GITHUB_USER_URL: lambda: httpx.Response(500)},
    "profile without id": {GITHUB_USER_URL: lambda: httpx.Response(200, json={"login": "ada"})},
    "emails 403": {GITHUB_EMAILS_URL: lambda: httpx.Response(403, json={"message": "no"})},
    "emails not a list": {GITHUB_EMAILS_URL: lambda: httpx.Response(200, json={"a": 1})},
}


@pytest.mark.parametrize("failure", PROVIDER_FAILURES)
async def test_a_provider_failure_redirects_with_a_provider_error(
    oauth_client: httpx.AsyncClient,
    providers: FakeProviders,
    session_factory: SessionFactory,
    failure: str,
) -> None:
    providers.overrides.update(PROVIDER_FAILURES[failure])

    response = await _sign_in(oauth_client)

    _went_to(response, "/login?error=OAUTH_PROVIDER_ERROR")
    assert not _has_session_cookies(oauth_client)
    assert await _users(session_factory) == []
    assert oauth_client.cookies.get("spl_oauth") is None


async def test_a_google_failure_redirects_with_a_provider_error(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    providers.overrides[GOOGLE_TOKEN_URL] = lambda: httpx.Response(401, json={"error": "x"})

    _went_to(await _sign_in(oauth_client, "google"), "/login?error=OAUTH_PROVIDER_ERROR")

    providers.overrides.clear()
    providers.overrides[GOOGLE_USERINFO_URL] = lambda: httpx.Response(503)
    _went_to(await _sign_in(oauth_client, "google"), "/login?error=OAUTH_PROVIDER_ERROR")


async def test_a_user_who_denies_access_gets_a_provider_error(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    started = await _start(oauth_client)
    state = _query(started.headers["location"])["state"]

    response = await _callback(oauth_client, error="access_denied", state=state)

    _went_to(response, "/login?error=OAUTH_PROVIDER_ERROR")
    assert providers.requests == []


async def test_a_callback_without_a_code_gets_a_provider_error(
    oauth_client: httpx.AsyncClient,
) -> None:
    started = await _start(oauth_client)
    state = _query(started.headers["location"])["state"]

    _went_to(await _callback(oauth_client, state=state), "/login?error=OAUTH_PROVIDER_ERROR")


# --- the exchange ------------------------------------------------------------------------------


async def test_the_token_request_carries_the_pkce_verifier(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    started = await _start(oauth_client)
    params = _query(started.headers["location"])

    await _callback(oauth_client, code="the-code", state=params["state"])

    form = providers.token_request_form(GITHUB_TOKEN_URL)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == f"{TEST_ORIGIN}{PREFIX}/github/callback"
    assert form["client_id"] == "github-client-id"
    assert form["client_secret"] == "github-client-secret"
    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]
    (request,) = providers.requests_to(GITHUB_TOKEN_URL)
    assert request.headers["accept"] == "application/json"


async def test_the_google_token_request_carries_the_pkce_verifier(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    started = await _start(oauth_client, "google")
    params = _query(started.headers["location"])

    await _callback(oauth_client, "google", code="g-code", state=params["state"])

    form = providers.token_request_form(GOOGLE_TOKEN_URL)
    assert form["code"] == "g-code"
    assert form["redirect_uri"] == f"{TEST_ORIGIN}{PREFIX}/google/callback"
    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]


async def test_the_provider_access_token_goes_only_to_the_provider(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    response = await _sign_in(oauth_client)

    assert "provider-access-token" not in str(response.headers)
    assert "provider-access-token" not in str(list(oauth_client.cookies.items()))
    sent = {r.headers.get("authorization") for r in providers.requests_to(GITHUB_USER_URL)}
    assert sent == {"Bearer provider-access-token"}


async def test_calls_to_the_provider_time_out_after_10_seconds(
    oauth_client: httpx.AsyncClient, providers: FakeProviders
) -> None:
    await _sign_in(oauth_client)

    assert {r.extensions["timeout"]["read"] for r in providers.requests} == {10.0}


async def test_github_uses_the_primary_email_and_its_verified_flag(
    oauth_client: httpx.AsyncClient, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    providers.github_emails = [
        {"email": "secondary@example.com", "primary": False, "verified": True},
        {"email": "primary@example.com", "primary": True, "verified": True},
    ]

    await _sign_in(oauth_client)

    (user,) = await _users(session_factory)
    assert user.email == "primary@example.com"


# --- next ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "evil.example",
        "/ok\r\nSet-Cookie: pwned=1",
    ],
)
async def test_an_unsafe_next_goes_to_the_root(
    oauth_client: httpx.AsyncClient, unsafe: str
) -> None:
    response = await _sign_in(oauth_client, next=unsafe)

    _went_to(response, "/")
    assert "pwned" not in str(response.headers)


async def test_a_safe_next_keeps_its_query(oauth_client: httpx.AsyncClient) -> None:
    response = await _sign_in(oauth_client, next="/traces?env=prod")

    _went_to(response, "/traces?env=prod")


# --- who may link: verified accounts only, and never the demo account ---------------------------

RESET = "/api/v1/auth/password/reset"
NEW_PASSWORD = "a brand new passphrase"


async def _add_identity(
    factory: SessionFactory, user_id: str, provider: str = "github", subject: str = "1001"
) -> None:
    """An identity attached by some path that skipped the link checks (an older release, a
    time before email was configured), so the reset test does not depend on how it got there."""
    async with factory() as db:
        db.add(OAuthIdentity(user_id=uuid.UUID(user_id), provider=provider, subject=subject))
        await db.commit()


async def _reset_password(client: httpx.AsyncClient, factory: SessionFactory, user_id: str) -> None:
    async with factory() as db:
        token = await issue_token(db, uuid.UUID(user_id), EmailTokenKind.RESET, timedelta(hours=1))
        await db.commit()
    response = await client.post(RESET, json={"token": token, "password": NEW_PASSWORD})
    assert response.status_code == 204, response.text


async def test_an_unverified_account_cannot_start_a_link_when_email_is_configured(
    oauth_browser_factory: BrowserFactory, providers: FakeProviders, session_factory: SessionFactory
) -> None:
    browser = await oauth_browser_factory("victim@example.com")  # signed up, never verified

    response = await _start(browser.http, intent="link")

    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_UNVERIFIED"
    assert "spl_oauth" not in response.headers.get("set-cookie", "")
    assert await _identities(session_factory) == []
    assert providers.requests == []


async def test_a_link_callback_for_an_unverified_account_is_refused(
    oauth_browser_factory: BrowserFactory,
    oauth_settings: Settings,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    # Skip `start` (its 409) with a state that is correctly signed, as if the link was begun
    # before the operator turned email on: the callback decides again.
    browser = await oauth_browser_factory("victim@example.com")
    cookie = _forged_cookie(
        oauth_settings,
        state="the-state",
        intent="link",
        user_id=uuid.UUID(browser.user["id"]),
        next="/settings/security",
    )
    browser.http.cookies.set("spl_oauth", cookie, domain="testserver.local", path=PREFIX)

    response = await _callback(browser.http, code="c", state="the-state")

    _went_to(response, "/settings/security?error=ACCOUNT_EMAIL_UNVERIFIED")
    assert await _identities(session_factory) == []


async def test_a_verified_account_can_still_link(
    verified_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    browser = await verified_browser_factory("ada@example.com")

    _went_to(await _sign_in(browser.http, intent="link"), "/settings/security")

    assert len(await _identities(session_factory)) == 1


async def test_an_unverified_account_can_link_when_email_is_not_configured(
    no_email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    # Without email nobody can verify an address, and nobody can run the email recovery that
    # makes linking from an unverified account dangerous, so refusing would only lock the
    # feature out of installations that have no email.
    browser = await no_email_browser_factory("ada@example.com")

    response = await _sign_in(browser.http, intent="link")

    _went_to(response, "/settings/security")
    assert len(await _identities(session_factory)) == 1


async def _demo_link_start(client: httpx.AsyncClient, session_factory: SessionFactory) -> None:
    assert (await client.post("/api/v1/demo/session")).status_code == 200

    response = await _start(client, intent="link")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert "spl_oauth" not in response.headers.get("set-cookie", "")
    assert await _identities(session_factory) == []


async def test_the_demo_account_cannot_link(
    oauth_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await _demo_link_start(oauth_client, session_factory)


async def test_the_demo_account_cannot_link_even_without_email(
    no_email_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    # With email configured the demo account is also refused for being unverified; without it,
    # only the demo check stands in the way.
    await _demo_link_start(no_email_client, session_factory)


async def test_a_link_callback_for_the_demo_account_is_refused(
    no_email_client: httpx.AsyncClient,
    oauth_settings_without_email: Settings,
    session_factory: SessionFactory,
) -> None:
    assert (await no_email_client.post("/api/v1/demo/session")).status_code == 200
    me = (await no_email_client.get(ME)).json()
    cookie = _forged_cookie(
        oauth_settings_without_email,
        state="the-state",
        intent="link",
        user_id=uuid.UUID(me["user"]["id"]),
        next="/settings/security",
    )
    no_email_client.cookies.set("spl_oauth", cookie, domain="testserver.local", path=PREFIX)

    response = await _callback(no_email_client, code="c", state="the-state")

    _went_to(response, "/settings/security?error=ACCOUNT_EMAIL_UNVERIFIED")
    assert await _identities(session_factory) == []


async def test_the_pre_hijack_chain_is_closed(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    providers: FakeProviders,
    session_factory: SessionFactory,
) -> None:
    """Attacker registers the victim's address, attaches GitHub, victim recovers the account.

    The attacker's GitHub account must not end up able to sign in to the victim's account.
    """
    attacker = await oauth_browser_factory("victim@example.com")
    assert (await attacker.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201
    victim_account = attacker.user["id"]

    # 1. The attacker cannot attach their GitHub to the unverified account...
    assert (await _start(attacker.http, intent="link")).status_code == 409
    assert await _identities(session_factory) == []

    # 2. ...even if some other path had attached it, the recovery removes it (defence in depth).
    await _add_identity(session_factory, victim_account)
    await _reset_password(oauth_client, session_factory, victim_account)
    assert await _identities(session_factory) == []

    # 3. The attacker's GitHub, with the attacker's own address, now signs in to nothing of the
    #    victim's: it creates a separate account.
    providers.set_github_email("attacker@example.com")
    _went_to(await _sign_in(oauth_client), "/")
    me = (await oauth_client.get(ME)).json()
    assert me["user"]["email"] == "attacker@example.com"
    assert me["user"]["id"] != victim_account


async def test_a_reset_that_verifies_the_address_removes_the_linked_identities(
    oauth_client: httpx.AsyncClient,
    oauth_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    browser = await oauth_browser_factory("victim@example.com")
    assert (await browser.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201
    await _add_identity(session_factory, browser.user["id"], "github", "1001")
    await _add_identity(session_factory, browser.user["id"], "google", "g-1")

    await _reset_password(oauth_client, session_factory, browser.user["id"])

    assert await _identities(session_factory) == []
    async with session_factory() as db:
        events = (
            await db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "user.oauth_unlink")
                .order_by(AuditEvent.created_at)
            )
        ).all()
    assert [event.metadata_ for event in events] == [
        {"provider": "github", "reason": "password_reset"},
        {"provider": "google", "reason": "password_reset"},
    ]
    assert {(e.actor_user_id, e.target_id) for e in events} == {
        (uuid.UUID(browser.user["id"]), browser.user["id"])
    }
    (user,) = await _users(session_factory)
    assert user.email_verified_at is not None


async def test_a_reset_keeps_the_identities_of_an_already_verified_account(
    oauth_client: httpx.AsyncClient,
    verified_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
) -> None:
    browser = await verified_browser_factory("ada@example.com")
    assert (await browser.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201
    await _add_identity(session_factory, browser.user["id"])

    await _reset_password(oauth_client, session_factory, browser.user["id"])

    assert len(await _identities(session_factory)) == 1
    assert "user.oauth_unlink" not in await _audit_actions(session_factory)
