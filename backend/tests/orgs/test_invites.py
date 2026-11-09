"""Invites that are also mailed: the optional address, the queued email and its per-org limit."""

from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditAction, AuditEvent, Invite, NotificationOutbox
from app.db.rls import bypass_rls
from tests.conftest import BrowserFactory
from tests.helpers import Browser, create_workspace, join_with_role, verify_email

SessionFactory = async_sessionmaker[AsyncSession]

MAX_EMAILED_INVITES_PER_HOUR = 20


def _invites_url(org_id: str) -> str:
    return f"/api/v1/orgs/{org_id}/invites"


async def _mailed_to(factory: SessionFactory, address: str) -> list[NotificationOutbox]:
    """The outbox rows addressed to `address` (signup also queues a verification email)."""
    async with factory() as db:
        rows = await db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        return [row for row in rows if row.target.get("to") == address]


async def _outbox_size(factory: SessionFactory) -> int:
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(NotificationOutbox)) or 0)


async def _count(factory: SessionFactory, model: Any, *where: Any) -> int:
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# --- mailing ---------------------------------------------------------------------------------


async def test_an_invite_with_an_email_queues_one_email_carrying_the_url(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "member", "email": "ada@example.com"},
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["url"].startswith("http://testserver/invite#token=")
    assert created["email"] == "ada@example.com"
    assert created["emailed"] is True
    (row,) = await _mailed_to(session_factory, "ada@example.com")
    assert created["url"] in row.payload["text"]
    assert created["url"] in row.payload["html"]
    assert row.kind == "email"


async def test_the_queued_email_names_the_inviter_the_org_and_the_role(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com", name="Grace Hopper")
    workspace = await create_workspace(owner, org_name="Navy Labs")

    await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "admin", "email": "ada@example.com"},
    )

    (row,) = await _mailed_to(session_factory, "ada@example.com")
    for part in ("Grace Hopper", "Navy Labs", "admin"):
        assert part in row.payload["text"]
    assert "Grace Hopper" in row.payload["subject"]
    assert "Navy Labs" in row.payload["subject"]


async def test_the_invite_email_html_escapes_the_org_name(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="<script>alert(1)</script>")

    await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "member", "email": "ada@example.com"},
    )

    (row,) = await _mailed_to(session_factory, "ada@example.com")
    assert "<script>" not in row.payload["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in row.payload["html"]


async def test_an_invite_without_an_email_queues_nothing(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    before = await _outbox_size(session_factory)

    response = await owner.post(
        _invites_url(workspace.org_id), json={"password": "correct horse battery", "role": "member"}
    )

    assert response.status_code == 201, response.text
    assert response.json()["email"] is None
    assert response.json()["emailed"] is False
    assert response.json()["url"].startswith("http://testserver/invite#token=")
    assert await _outbox_size(session_factory) == before


async def test_an_invite_with_an_email_but_no_email_configured_queues_nothing(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "member", "email": "ada@example.com"},
    )

    # The invite still works as a link; only the mailing is skipped.
    assert response.status_code == 201, response.text
    assert response.json()["emailed"] is False
    assert response.json()["url"].startswith("http://testserver/invite#token=")
    assert await _outbox_size(session_factory) == 0
    assert await _count(session_factory, Invite) == 1


async def test_a_bad_email_is_rejected_and_creates_nothing(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "member", "email": "not-an-address"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert await _count(session_factory, Invite) == 0


async def test_a_refused_invite_is_not_mailed(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    admin: Browser = await email_browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")

    response = await admin.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "owner", "email": "ada@example.com"},
    )

    assert response.status_code == 403
    assert await _mailed_to(session_factory, "ada@example.com") == []


async def test_the_invite_the_audit_event_and_the_email_commit_together(
    email_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    async def failing_enqueue(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("app.services.invites.enqueue", failing_enqueue)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await owner.post(
            _invites_url(workspace.org_id),
            json={
                "password": "correct horse battery",
                "role": "member",
                "email": "ada@example.com",
            },
        )

    assert await _count(session_factory, Invite) == 0
    assert (
        await _count(
            session_factory, AuditEvent, AuditEvent.action == AuditAction.INVITE_CREATE.value
        )
        == 0
    )


# --- listing and accepting -------------------------------------------------------------------


async def test_listing_invites_shows_the_email_or_null(
    email_browser_factory: BrowserFactory,
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    mailed = (
        await owner.post(
            _invites_url(workspace.org_id),
            json={
                "password": "correct horse battery",
                "role": "member",
                "email": "ada@example.com",
            },
        )
    ).json()
    shared = (
        await owner.post(
            _invites_url(workspace.org_id),
            json={"password": "correct horse battery", "role": "viewer"},
        )
    ).json()

    listed = (await owner.get(_invites_url(workspace.org_id))).json()

    assert {item["id"]: item["email"] for item in listed} == {
        mailed["id"]: "ada@example.com",
        shared["id"]: None,
    }


async def test_mailing_does_not_change_who_can_accept(
    email_browser_factory: BrowserFactory,
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    someone_else: Browser = await email_browser_factory("bob@example.com")
    workspace = await create_workspace(owner)
    created = (
        await owner.post(
            _invites_url(workspace.org_id),
            json={
                "password": "correct horse battery",
                "role": "member",
                "email": "ada@example.com",
            },
        )
    ).json()
    token = created["url"].split("#token=", 1)[1]

    # Any signed-in holder of the link may accept, whatever address it was mailed to.
    accepted = await someone_else.post("/api/v1/invites/accept", json={"token": token})

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "member"


# --- the token stays out of the URL path and query ------------------------------------------


async def test_the_invite_link_carries_the_token_in_the_fragment(
    browser_factory: BrowserFactory,
) -> None:
    """A fragment reaches no server: not the access logs, not a `Referer` (Ruling P5)."""
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    created = (
        await owner.post(
            _invites_url(workspace.org_id),
            json={"password": "correct horse battery", "role": "member"},
        )
    ).json()

    base, separator, token = created["url"].partition("#token=")
    assert (base, separator) == ("http://testserver/invite", "#token=")
    assert token and "/" not in token and "?" not in token


async def test_the_preview_takes_the_token_in_a_post_body_not_the_query(
    browser_factory: BrowserFactory,
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    guest: Browser = await browser_factory("guest@example.com")
    workspace = await create_workspace(owner, org_name="Acme AI")
    created = (
        await owner.post(
            _invites_url(workspace.org_id),
            json={"password": "correct horse battery", "role": "admin"},
        )
    ).json()
    token = created["url"].split("#token=", 1)[1]

    preview = await guest.post("/api/v1/invites/preview", json={"token": token})
    by_query = await guest.get("/api/v1/invites/preview", params={"token": token})

    assert preview.status_code == 200, preview.text
    assert preview.json()["org"]["name"] == "Acme AI"
    assert preview.json()["role"] == "admin"
    assert by_query.status_code == 405


# --- the per-org limit -----------------------------------------------------------------------


async def _send_invites(owner: Browser, org_id: str, count: int, *, prefix: str = "guest") -> None:
    for number in range(count):
        response = await owner.post(
            _invites_url(org_id),
            json={
                "password": "correct horse battery",
                "role": "viewer",
                "email": f"{prefix}{number}@example.com",
            },
        )
        assert response.status_code == 201, response.text


async def test_the_21st_emailed_invite_in_an_hour_is_refused_and_creates_nothing(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await _send_invites(owner, workspace.org_id, MAX_EMAILED_INVITES_PER_HOUR)
    outbox_before = await _outbox_size(session_factory)

    refused = await owner.post(
        _invites_url(workspace.org_id),
        json={"password": "correct horse battery", "role": "viewer", "email": "late@example.com"},
    )

    assert refused.status_code == 429
    assert refused.json()["code"] == "RATE_LIMITED"
    assert 0 < int(refused.headers["Retry-After"]) <= 3600
    assert await _count(session_factory, Invite) == MAX_EMAILED_INVITES_PER_HOUR
    assert (
        await _count(
            session_factory, AuditEvent, AuditEvent.action == AuditAction.INVITE_CREATE.value
        )
        == MAX_EMAILED_INVITES_PER_HOUR
    )
    assert await _outbox_size(session_factory) == outbox_before
    assert await _mailed_to(session_factory, "late@example.com") == []


async def test_invites_without_an_email_are_not_limited(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    extra = 3

    for _ in range(MAX_EMAILED_INVITES_PER_HOUR + extra):
        response = await owner.post(
            _invites_url(workspace.org_id),
            json={"password": "correct horse battery", "role": "viewer"},
        )
        assert response.status_code == 201, response.text

    # Link-only invites used none of the budget, so mailing still has all of it.
    await _send_invites(owner, workspace.org_id, MAX_EMAILED_INVITES_PER_HOUR)
    assert await _count(session_factory, Invite) == 2 * MAX_EMAILED_INVITES_PER_HOUR + extra


async def test_the_limit_holds_per_org(email_browser_factory: BrowserFactory) -> None:
    owner: Browser = await email_browser_factory("owner@example.com")
    first = await create_workspace(owner, org_name="First")
    second = await create_workspace(owner, org_name="Second")
    await _send_invites(owner, first.org_id, MAX_EMAILED_INVITES_PER_HOUR)

    blocked = await owner.post(
        _invites_url(first.org_id),
        json={"password": "correct horse battery", "role": "viewer", "email": "late@example.com"},
    )
    other_org = await owner.post(
        _invites_url(second.org_id),
        json={"password": "correct horse battery", "role": "viewer", "email": "late@example.com"},
    )

    assert blocked.status_code == 429
    assert other_org.status_code == 201, other_org.text


async def test_the_limit_is_not_used_up_when_email_is_not_configured(
    browser_factory: BrowserFactory,
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    # Nothing is mailed here, so nothing is counted and nothing is refused.
    await _send_invites(owner, workspace.org_id, MAX_EMAILED_INVITES_PER_HOUR + 2)


async def test_a_stolen_session_cannot_create_an_invite(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "owner"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"
    async with session_factory() as db:
        await bypass_rls(db)
        assert await db.scalar(text("SELECT count(*) FROM invites")) == 0


async def test_a_stolen_session_cannot_change_a_role(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    other = await browser_factory("other@example.com")
    await join_with_role(owner, other, workspace.org_id, "viewer")

    response = await owner.patch(
        f"/api/v1/orgs/{workspace.org_id}/members/{other.user['id']}", json={"role": "owner"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"


async def test_six_successful_invites_do_not_use_up_reauth_attempts(
    browser_factory: BrowserFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    for _ in range(6):
        response = await owner.post(
            _invites_url(workspace.org_id),
            json={"role": "viewer", "password": "correct horse battery"},
        )
        assert response.status_code == 201, response.text


async def test_the_sixth_wrong_invite_password_is_throttled(
    browser_factory: BrowserFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    for _ in range(5):
        response = await owner.post(
            _invites_url(workspace.org_id), json={"role": "viewer", "password": "wrong password"}
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "INVALID_PASSWORD"
    refused = await owner.post(
        _invites_url(workspace.org_id), json={"role": "viewer", "password": "wrong password"}
    )
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "RATE_LIMITED"


async def test_accepting_an_invite_notifies_the_inviter_but_not_the_joiner(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await email_browser_factory("owner@example.com", name="Grace Hopper")
    joiner = await email_browser_factory("joiner@example.com", name="Ada Lovelace")
    await verify_email(session_factory, "owner@example.com")
    workspace = await create_workspace(owner, org_name="Navy Labs")
    owner_before = len(await _mailed_to(session_factory, "owner@example.com"))
    joiner_before = len(await _mailed_to(session_factory, "joiner@example.com"))

    await join_with_role(owner, joiner, workspace.org_id, "member")

    owner_mail = await _mailed_to(session_factory, "owner@example.com")
    assert len(owner_mail) == owner_before + 1
    notice = next(row for row in owner_mail if "joined" in row.payload["subject"])
    assert "Ada Lovelace" in notice.payload["text"]
    assert "joiner@example.com" in notice.payload["text"]
    assert "member" in notice.payload["text"]
    assert "Grace Hopper" in notice.payload["text"]
    assert "Navy Labs" in notice.payload["text"]
    assert "<a " not in notice.payload["html"]
    assert len(await _mailed_to(session_factory, "joiner@example.com")) == joiner_before
