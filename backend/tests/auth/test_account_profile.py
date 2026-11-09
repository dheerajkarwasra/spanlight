"""The caller's own profile: editing the name, and changing the email address with a link.

Changing the address re-proves the owner (password, or a fresh sign-in for an account without
one), mails the new address a link and the old one a notice, and switches only when the link is
opened: then every session ends.
"""

import re
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.email_tokens import issue_token
from app.auth.totp import hash_recovery_code
from app.db.models import (
    AuditAction,
    AuditEvent,
    EmailToken,
    EmailTokenKind,
    NotificationOutbox,
    PersonalAccessToken,
    RecoveryCode,
    User,
)
from tests.auth.conftest import FakeClock
from tests.auth.test_totp_routes import enroll, verify_email
from tests.conftest import BrowserFactory
from tests.helpers import Browser, bearer, create_token

SessionFactory = async_sessionmaker[AsyncSession]

ME = "/api/v1/auth/me"
CHANGE = "/api/v1/auth/email/change"
CONFIRM = "/api/v1/auth/email/change/confirm"
PASSWORD = "correct horse battery"  # what the browser factory signs users up with
NEW_EMAIL = "ada.new@example.com"
TOKEN_IN_TEXT = re.compile(r"/confirm-email-change#token=([A-Za-z0-9_-]+)")


async def _outbox(factory: SessionFactory) -> list[NotificationOutbox]:
    async with factory() as db:
        return list(
            (await db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))).all()
        )


async def _change(browser: Browser, new_email: str = NEW_EMAIL, **extra: object) -> httpx.Response:
    return await browser.post(CHANGE, json={"new_email": new_email, "password": PASSWORD, **extra})


async def _latest_token(factory: SessionFactory) -> str:
    """The token in the newest confirm link that was mailed."""
    links = [
        match.group(1)
        for row in await _outbox(factory)
        if (match := TOKEN_IN_TEXT.search(row.payload["text"]))
    ]
    assert links
    return links[-1]


async def _user(factory: SessionFactory, user_id: str) -> User:
    async with factory() as db:
        user = await db.get(User, uuid.UUID(user_id))
        assert user is not None
        return user


async def _execute(factory: SessionFactory, statement: str, **params: object) -> None:
    async with factory() as db:
        await db.execute(text(statement), params)
        await db.commit()


async def _audit(factory: SessionFactory, action: AuditAction) -> list[AuditEvent]:
    async with factory() as db:
        return list(
            (await db.scalars(select(AuditEvent).where(AuditEvent.action == action.value))).all()
        )


# --- name ------------------------------------------------------------------------------------


async def test_patch_me_updates_name_and_audits(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    org = await ada.post("/api/v1/orgs", json={"name": "Acme"})
    assert org.status_code == 201, org.text

    response = await ada.patch(ME, json={"name": "  Ada Lovelace "})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Ada Lovelace"
    assert response.json()["email"] == "ada@example.com"
    assert (await _user(session_factory, ada.user["id"])).name == "Ada Lovelace"
    (event,) = await _audit(session_factory, AuditAction.USER_UPDATE)
    assert str(event.org_id) == org.json()["id"]
    assert event.target_id == ada.user["id"]
    assert str(event.actor_user_id) == ada.user["id"]

    assert (await ada.patch(ME, json={"name": "   "})).status_code == 422


# --- requesting a change ---------------------------------------------------------------------


async def test_email_change_wrong_password(email_browser_factory: BrowserFactory) -> None:
    ada = await email_browser_factory("ada@example.com")

    response = await _change(ada, password="not the password")

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"


async def test_stolen_session_cannot_change_email(email_browser_factory: BrowserFactory) -> None:
    ada = await email_browser_factory("ada@example.com")

    response = await ada.post(CHANGE, json={"new_email": NEW_EMAIL})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"


async def test_email_change_oauth_only_stale_session(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await email_browser_factory("ada@example.com")
    await _execute(
        session_factory, "UPDATE users SET password_hash = NULL WHERE id = :id", id=ada.user["id"]
    )
    fresh = await ada.post(CHANGE, json={"new_email": NEW_EMAIL})
    assert fresh.status_code == 202, fresh.text

    await _execute(
        session_factory,
        "UPDATE sessions SET created_at = now() - interval '11 minutes' WHERE user_id = :id",
        id=ada.user["id"],
    )
    # Six times: a stale sign-in tests no secret, so it must not use up the attempts.
    for _ in range(6):
        stale = await ada.post(CHANGE, json={"new_email": NEW_EMAIL})
        assert stale.status_code == 403, stale.text
        assert stale.json()["code"] == "REAUTH_REQUIRED"


async def test_email_change_taken_address(email_browser_factory: BrowserFactory) -> None:
    ada = await email_browser_factory("ada@example.com")
    await email_browser_factory("grace@example.com")

    response = await _change(ada, "GRACE@example.com")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EMAIL_TAKEN"


async def test_email_change_without_email_provider(browser_factory: BrowserFactory) -> None:
    ada = await browser_factory("ada@example.com")

    response = await _change(ada)

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "EMAIL_NOT_CONFIGURED"


async def test_email_change_sends_link_to_new_and_notice_to_old(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await email_browser_factory("ada@example.com", "Ada")
    before = len(await _outbox(session_factory))  # the verification email from signup

    response = await _change(ada)

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted"}
    rows = (await _outbox(session_factory))[before:]
    assert len(rows) == 2
    link = next(row for row in rows if row.target == {"to": NEW_EMAIL})
    notice = next(row for row in rows if row.target == {"to": "ada@example.com"})
    assert "Hi Ada," in link.payload["text"]
    assert "http://testserver/confirm-email-change#token=" in link.payload["text"]
    assert "/confirm-email-change#token=" in link.payload["html"]
    assert "Hi Ada," in notice.payload["text"]
    assert "token" not in notice.payload["text"]
    assert "http" not in notice.payload["text"]
    assert NEW_EMAIL not in notice.payload["text"]
    assert "\n" not in link.payload["subject"]
    assert "\n" not in notice.payload["subject"]
    # Nothing changes until the link is opened.
    assert (await _user(session_factory, ada.user["id"])).email == "ada@example.com"


async def test_email_change_retires_previous_tokens(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await email_browser_factory("ada@example.com")
    assert (await _change(ada, "first@example.com")).status_code == 202
    first = await _latest_token(session_factory)
    assert (await _change(ada, "second@example.com")).status_code == 202

    async with session_factory() as db:
        tokens = (
            await db.scalars(
                select(EmailToken)
                .where(EmailToken.kind == EmailTokenKind.EMAIL_CHANGE)
                .order_by(EmailToken.created_at)
            )
        ).all()
    assert [token.new_email for token in tokens] == ["first@example.com", "second@example.com"]
    assert tokens[0].used_at is not None
    assert tokens[1].used_at is None
    assert tokens[1].expires_at - tokens[1].created_at == timedelta(hours=1)
    assert (await email_client.post(CONFIRM, json={"token": first})).status_code == 404


async def test_email_change_rate_limited(email_browser_factory: BrowserFactory) -> None:
    ada = await email_browser_factory("ada@example.com")
    for _ in range(3):
        assert (await _change(ada)).status_code == 202

    response = await _change(ada)

    assert response.status_code == 429, response.text
    assert "retry-after" in response.headers


async def test_profile_routes_refuse_demo_user(email_client: httpx.AsyncClient) -> None:
    assert (await email_client.post("/api/v1/demo/session")).status_code == 200
    csrf = {"X-CSRF-Token": email_client.cookies["spl_csrf"]}

    renamed = await email_client.patch(ME, json={"name": "Visitor"}, headers=csrf)
    changed = await email_client.post(
        CHANGE, json={"new_email": NEW_EMAIL, "password": PASSWORD}, headers=csrf
    )

    for response in (renamed, changed):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"


# --- two-factor re-proof --------------------------------------------------------------------


async def _two_factor_ada(
    factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> tuple[Browser, str, list[str]]:
    """Ada with a verified address and two-factor authentication on: secret, recovery codes."""
    ada = await factory("ada@example.com")
    await verify_email(session_factory, "ada@example.com")
    secret, codes = await enroll(ada, clock)
    return ada, secret, codes


async def test_email_change_with_2fa_needs_the_code(
    totp_email_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    # A stolen session that also knows the password is still missing the second factor.
    ada, _, _ = await _two_factor_ada(totp_email_browser_factory, session_factory, clock)
    before = len(await _outbox(session_factory))

    response = await _change(ada)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "TOTP_CODE_REQUIRED"
    assert len(await _outbox(session_factory)) == before


async def test_email_change_wrong_code_counts_toward_the_limit(
    totp_email_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada, _, _ = await _two_factor_ada(totp_email_browser_factory, session_factory, clock)
    before = len(await _outbox(session_factory))

    for _ in range(5):
        wrong = await _change(ada, code="123456")
        assert wrong.status_code == 422, wrong.text
        assert wrong.json()["code"] == "INVALID_TOTP_CODE"
    blocked = await _change(ada, code="123456")

    assert blocked.status_code == 429, blocked.text
    assert len(await _outbox(session_factory)) == before


async def test_email_change_with_a_valid_totp_code(
    totp_email_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada, secret, _ = await _two_factor_ada(totp_email_browser_factory, session_factory, clock)

    response = await _change(ada, code=clock.next_code(secret))

    assert response.status_code == 202, response.text


@pytest.mark.parametrize("code", ["abcde-fghij", "abcdefg-hijklmn-opqrstu-vwxyz23"])
async def test_email_change_recovery_code_works_once(
    totp_email_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
    clock: FakeClock,
    code: str,
) -> None:
    ada, _, _ = await _two_factor_ada(totp_email_browser_factory, session_factory, clock)
    async with session_factory() as db:
        db.add(RecoveryCode(user_id=uuid.UUID(ada.user["id"]), code_hash=hash_recovery_code(code)))
        await db.commit()

    first = await _change(ada, code=f"  {code.upper()}  ")
    again = await _change(ada, code=code)

    assert first.status_code == 202, first.text
    assert again.status_code == 422, again.text
    assert again.json()["code"] == "INVALID_TOTP_CODE"


# --- confirming ------------------------------------------------------------------------------


async def test_confirm_switches_email_and_ends_sessions(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await email_browser_factory("ada@example.com")
    org = await ada.post("/api/v1/orgs", json={"name": "Acme"})
    assert org.status_code == 201, org.text
    assert (await _change(ada)).status_code == 202

    response = await email_client.post(
        CONFIRM, json={"token": await _latest_token(session_factory)}
    )

    assert response.status_code == 204, response.text
    assert (await ada.get(ME)).status_code == 401
    user = await _user(session_factory, ada.user["id"])
    assert user.email == NEW_EMAIL
    assert user.email_verified_at is not None
    (event,) = await _audit(session_factory, AuditAction.USER_EMAIL_CHANGE)
    assert str(event.org_id) == org.json()["id"]
    assert "@" not in repr(event.metadata_)
    assert event.ip is not None
    login = await email_client.post(
        "/api/v1/auth/login", json={"email": NEW_EMAIL, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text


async def test_confirm_revokes_the_users_access_tokens_only(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await email_browser_factory("ada@example.com")
    grace = await email_browser_factory("grace@example.com")
    assert (await ada.post("/api/v1/orgs", json={"name": "Acme"})).status_code == 201
    adas = await create_token(ada)
    graces = await create_token(grace)
    assert (await _change(ada)).status_code == 202

    response = await email_client.post(
        CONFIRM, json={"token": await _latest_token(session_factory)}
    )

    assert response.status_code == 204, response.text
    async with session_factory() as db:
        revoked = dict(
            (await db.execute(select(PersonalAccessToken.id, PersonalAccessToken.revoked_at))).all()
        )
    assert revoked[uuid.UUID(adas["id"])] is not None
    assert revoked[uuid.UUID(graces["id"])] is None
    assert (await email_client.get(ME, headers=bearer(adas["token"]))).status_code == 401
    assert (await email_client.get(ME, headers=bearer(graces["token"]))).status_code == 200
    (event,) = await _audit(session_factory, AuditAction.USER_EMAIL_CHANGE)
    assert event.metadata_["tokens_revoked"] == 1


async def test_confirm_reused_or_expired_token(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await email_browser_factory("ada@example.com")
    assert (await _change(ada)).status_code == 202
    token = await _latest_token(session_factory)
    assert (await email_client.post(CONFIRM, json={"token": token})).status_code == 204

    reused = await email_client.post(CONFIRM, json={"token": token})

    async with session_factory() as db:
        expired = await issue_token(
            db,
            uuid.UUID(ada.user["id"]),
            EmailTokenKind.EMAIL_CHANGE,
            timedelta(seconds=-1),
            new_email="later@example.com",
        )
        await db.commit()
    late = await email_client.post(CONFIRM, json={"token": expired})

    # The same answer reset confirm gives for a bad token.
    for response in (reused, late):
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "NOT_FOUND"
    assert (await _user(session_factory, ada.user["id"])).email == NEW_EMAIL


async def test_confirm_address_taken_meanwhile(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await email_browser_factory("ada@example.com")
    assert (await _change(ada)).status_code == 202
    await email_browser_factory(NEW_EMAIL)

    response = await email_client.post(
        CONFIRM, json={"token": await _latest_token(session_factory)}
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EMAIL_TAKEN"
    assert (await _user(session_factory, ada.user["id"])).email == "ada@example.com"
    assert (await ada.get(ME)).status_code == 200


async def test_password_reset_cancels_pending_email_change(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    # Someone with a phished password asks to move the account; the owner answers by resetting.
    ada = await email_browser_factory("ada@example.com")
    assert (await _change(ada, "attacker@example.com")).status_code == 202
    change_token = await _latest_token(session_factory)
    forgot = await email_client.post(
        "/api/v1/auth/password/forgot", json={"email": "ada@example.com"}
    )
    assert forgot.status_code == 202, forgot.text
    reset_token = next(
        match.group(1)
        for row in reversed(await _outbox(session_factory))
        if (match := re.search(r"/reset-password#token=([A-Za-z0-9_-]+)", row.payload["text"]))
    )
    reset = await email_client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset_token, "password": "a brand new passphrase"},
    )
    assert reset.status_code == 204, reset.text

    response = await email_client.post(CONFIRM, json={"token": change_token})

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert (await _user(session_factory, ada.user["id"])).email == "ada@example.com"
