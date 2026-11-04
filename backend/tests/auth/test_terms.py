"""Terms acceptance: at signup, on `/me`, the anonymous lookup and the accept route.

With `TERMS_VERSION` set, signing up needs `accept_terms: true` and stores the server's own
version; unset, nothing asks for terms anywhere.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AuditAction, AuditEvent, User
from tests.conftest import BrowserFactory, _browsers, _make_client, _running
from tests.helpers import Browser, bearer, create_token

SessionFactory = async_sessionmaker[AsyncSession]

VERSION = "2026-11-01"
SIGNUP = "/api/v1/auth/signup"
ME = "/api/v1/auth/me"
TERMS = "/api/v1/auth/terms"
ACCEPT = "/api/v1/auth/terms/accept"
NEW_USER = {"email": "ada@example.com", "password": "correct horse battery", "name": "Ada"}


@pytest.fixture
async def terms_app(settings: Settings) -> AsyncIterator[Any]:
    async with _running(settings.model_copy(update={"terms_version": VERSION})) as application:
        yield application


@pytest.fixture
async def terms_client(terms_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _make_client(terms_app) as http:
        yield http


async def _signup(http: httpx.AsyncClient, **extra: Any) -> Browser:
    response = await http.post(SIGNUP, json={**NEW_USER, **extra})
    assert response.status_code == 201, response.text
    return Browser(http=http, user=response.json())


async def _signed_up_before_terms(http: httpx.AsyncClient, settings: Settings) -> Browser:
    """An account made before terms were configured, signed in on `http`: it accepted nothing."""
    async with _running(settings) as plain_app, _browsers(plain_app) as factory:
        await factory(NEW_USER["email"])
    login = await http.post(
        "/api/v1/auth/login", json={"email": NEW_USER["email"], "password": NEW_USER["password"]}
    )
    assert login.status_code == 200, login.text
    return Browser(http=http, user=login.json()["user"])


async def _stored_terms(session_factory: SessionFactory, email: str) -> tuple[Any, Any]:
    async with session_factory() as db:
        row = (
            await db.execute(
                select(User.terms_version, User.terms_accepted_at).where(User.email == email)
            )
        ).one()
    return row[0], row[1]


# --- signup ------------------------------------------------------------------------------------


async def test_signup_requires_accept_terms_when_configured(
    terms_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    for body in (NEW_USER, {**NEW_USER, "accept_terms": False}):
        response = await terms_client.post(SIGNUP, json=body)

        assert response.status_code == 422, response.text
        assert response.json()["code"] == "TERMS_NOT_ACCEPTED"
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_signup_accept_terms_must_be_json_true(
    terms_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    for value in ("yes", 1, "true"):
        response = await terms_client.post(SIGNUP, json={**NEW_USER, "accept_terms": value})

        assert response.status_code == 422, response.text
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_signup_stores_server_version(
    terms_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await _signup(terms_client, accept_terms=True, terms_version="1999-01-01")

    version, accepted_at = await _stored_terms(session_factory, NEW_USER["email"])
    assert version == VERSION
    assert accepted_at is not None


async def test_signup_without_terms_configured_needs_nothing(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await _signup(client)

    assert await _stored_terms(session_factory, NEW_USER["email"]) == (None, None)


# --- /me and the lookup ------------------------------------------------------------------------


async def test_me_reports_terms_versions(
    terms_client: httpx.AsyncClient, browser_factory: BrowserFactory
) -> None:
    ada = await _signup(terms_client, accept_terms=True)
    me = (await ada.get(ME)).json()
    assert me["terms_accepted_version"] == VERSION
    assert me["terms_current_version"] == VERSION

    grace = await browser_factory("grace@example.com")
    me = (await grace.get(ME)).json()
    assert me["terms_accepted_version"] is None
    assert me["terms_current_version"] is None


async def test_terms_lookup_is_anonymous(
    terms_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    configured = await terms_client.get(TERMS)
    assert configured.status_code == 200, configured.text
    assert configured.json() == {"version": VERSION}

    unset = await client.get(TERMS)
    assert unset.status_code == 200, unset.text
    assert unset.json() == {"version": None}


# --- accept ------------------------------------------------------------------------------------


async def test_accept_rejects_other_version(terms_client: httpx.AsyncClient) -> None:
    ada = await _signup(terms_client, accept_terms=True)

    response = await ada.post(ACCEPT, json={"version": "1999-01-01"})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "TERMS_VERSION_MISMATCH"


async def test_accept_when_unconfigured(browser_factory: BrowserFactory) -> None:
    ada = await browser_factory("ada@example.com")

    response = await ada.post(ACCEPT, json={"version": VERSION})

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "TERMS_NOT_CONFIGURED"


async def test_accept_is_idempotent(
    terms_client: httpx.AsyncClient, settings: Settings, session_factory: SessionFactory
) -> None:
    ada = await _signed_up_before_terms(terms_client, settings)
    assert (await ada.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201

    first = await ada.post(ACCEPT, json={"version": VERSION})
    assert first.status_code == 204, first.text
    version, accepted_at = await _stored_terms(session_factory, NEW_USER["email"])
    assert version == VERSION
    assert accepted_at is not None

    second = await ada.post(ACCEPT, json={"version": VERSION})
    assert second.status_code == 204, second.text
    assert await _stored_terms(session_factory, NEW_USER["email"]) == (VERSION, accepted_at)
    async with session_factory() as db:
        events = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.USER_TERMS_ACCEPT.value)
        )
    assert events == 1


async def test_accept_writes_audit_in_each_org(
    terms_client: httpx.AsyncClient, settings: Settings, session_factory: SessionFactory
) -> None:
    ada = await _signed_up_before_terms(terms_client, settings)
    orgs = [(await ada.post("/api/v1/orgs", json={"name": name})).json()["id"] for name in "AB"]

    response = await ada.post(ACCEPT, json={"version": VERSION})

    assert response.status_code == 204, response.text
    async with session_factory() as db:
        events = (
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == AuditAction.USER_TERMS_ACCEPT.value)
            )
        ).all()
    assert sorted(str(event.org_id) for event in events) == sorted(orgs)
    for event in events:
        assert event.target_type == "user"
        assert event.target_id == ada.user["id"]
        assert str(event.actor_user_id) == ada.user["id"]
        assert event.metadata_ == {"version": VERSION}


async def test_accept_refuses_demo_user(terms_client: httpx.AsyncClient) -> None:
    assert (await terms_client.post("/api/v1/demo/session")).status_code == 200
    csrf = {"X-CSRF-Token": terms_client.cookies["spl_csrf"]}

    response = await terms_client.post(ACCEPT, json={"version": VERSION}, headers=csrf)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"


async def test_me_asks_demo_user_for_no_terms(terms_client: httpx.AsyncClient) -> None:
    assert (await terms_client.post("/api/v1/demo/session")).status_code == 200

    me = (await terms_client.get(ME)).json()

    assert me["terms_current_version"] is None


async def test_accept_refuses_bearer_token(terms_app: Any, terms_client: httpx.AsyncClient) -> None:
    ada = await _signup(terms_client, accept_terms=True)
    token = await create_token(ada, scope="write")

    async with _make_client(terms_app) as http:
        response = await http.post(
            ACCEPT, json={"version": VERSION}, headers=bearer(token["token"])
        )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "SESSION_REQUIRED"
