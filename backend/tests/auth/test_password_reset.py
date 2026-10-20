"""Password reset: the forgot and reset routes, the emailed link, and what a reset does."""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import password_reset
from app.auth.email_tokens import issue_token
from app.auth.password_reset import reset_url
from app.config import Settings
from app.db.models import (
    AuditAction,
    AuditEvent,
    EmailToken,
    EmailTokenKind,
    NotificationOutbox,
    ThrottleEvent,
    User,
)
from app.services.demo import DEMO_USER_EMAIL, ensure_demo_workspace
from tests.conftest import BrowserFactory
from tests.helpers import Browser

SessionFactory = async_sessionmaker[AsyncSession]

FORGOT = "/api/v1/auth/password/forgot"
RESET = "/api/v1/auth/password/reset"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
OLD_PASSWORD = "correct horse battery"  # what the browser factory signs users up with
NEW_PASSWORD = "a brand new passphrase"
TOKEN_IN_TEXT = re.compile(r"/reset-password#token=([A-Za-z0-9_-]+)")


async def _outbox(factory: SessionFactory) -> list[NotificationOutbox]:
    async with factory() as db:
        return list(
            (await db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))).all()
        )


def _token_in(row: NotificationOutbox) -> str:
    match = TOKEN_IN_TEXT.search(row.payload["text"])
    assert match, row.payload["text"]
    return match.group(1)


async def _issue(
    factory: SessionFactory, user_id: str, kind: EmailTokenKind = EmailTokenKind.RESET
) -> str:
    async with factory() as db:
        raw = await issue_token(db, uuid.UUID(user_id), kind, timedelta(hours=1))
        await db.commit()
    return raw


async def _execute(factory: SessionFactory, statement: str) -> None:
    async with factory() as db:
        await db.execute(text(statement))
        await db.commit()


async def _count_throttle_events(
    factory: SessionFactory, scope: str, key: str | None = None
) -> int:
    async with factory() as db:
        statement = (
            select(func.count()).select_from(ThrottleEvent).where(ThrottleEvent.scope == scope)
        )
        if key is not None:
            statement = statement.where(ThrottleEvent.key == key)
        return int(await db.scalar(statement) or 0)


async def _verified_at(factory: SessionFactory, user_id: str) -> datetime | None:
    async with factory() as db:
        user = await db.get(User, uuid.UUID(user_id))
        assert user is not None
        return user.email_verified_at


async def _reset_tokens(factory: SessionFactory) -> list[EmailToken]:
    async with factory() as db:
        return list(
            (
                await db.scalars(
                    select(EmailToken)
                    .where(EmailToken.kind == EmailTokenKind.RESET)
                    .order_by(EmailToken.created_at)
                )
            ).all()
        )


async def _login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": password})


def _without_request_id(response: httpx.Response) -> dict[str, object]:
    body: dict[str, object] = response.json()
    body.pop("request_id", None)
    return body


# --- the link -------------------------------------------------------------------------------


def test_reset_url_puts_the_token_in_the_fragment(settings: Settings) -> None:
    # A fragment is never sent to a server or in a Referer header, so the token stays out of logs.
    assert reset_url(settings, "abc_-123") == "http://testserver/reset-password#token=abc_-123"


# --- forgot ---------------------------------------------------------------------------------


async def test_forgot_for_a_known_email_queues_one_reset_email(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com", "Ada")
    before = len(await _outbox(session_factory))  # the verification email from signup

    response = await email_client.post(FORGOT, json={"email": "ada@example.com"})

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    rows = await _outbox(session_factory)
    assert len(rows) == before + 1
    reset_email = next(row for row in rows if row.payload["subject"].startswith("Reset"))
    assert reset_email.kind == "email"
    assert reset_email.target == {"to": "ada@example.com"}
    assert reset_email.payload["subject"] == "Reset your Spanlight password"
    assert "Hi Ada," in reset_email.payload["text"]
    assert "http://testserver/reset-password#token=" in reset_email.payload["text"]
    assert "/reset-password#token=" in reset_email.payload["html"]


async def test_forgot_unknown_email_is_indistinguishable(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com", "Ada")
    queued_at_signup = len(await _outbox(session_factory))

    known = await email_client.post(FORGOT, json={"email": "ada@example.com"})
    after_known = await _outbox(session_factory)
    unknown = await email_client.post(FORGOT, json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content  # byte for byte
    assert known.headers["content-type"] == unknown.headers["content-type"]
    assert len(after_known) == queued_at_signup + 1  # the known address queued mail ...
    assert len(await _outbox(session_factory)) == len(after_known)  # ... the unknown one did not
    assert len(await _reset_tokens(session_factory)) == 1  # and issued no token either


async def test_forgot_finds_the_account_whatever_the_case_of_the_address(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com", "Ada")

    response = await email_client.post(FORGOT, json={"email": "ADA@Example.COM"})

    assert response.status_code == 202
    (token,) = await _reset_tokens(session_factory)
    assert token.used_at is None


async def test_forgot_sets_no_cookie_and_needs_no_session(
    email_client: httpx.AsyncClient,
) -> None:
    response = await email_client.post(FORGOT, json={"email": "nobody@example.com"})

    assert response.status_code == 202
    assert "set-cookie" not in response.headers
    assert not email_client.cookies


async def test_forgot_without_email_configured_is_not_configured_for_any_address(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await browser_factory("ada@example.com")

    known = await client.post(FORGOT, json={"email": "ada@example.com"})
    unknown = await client.post(FORGOT, json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 409
    assert known.json()["code"] == "NOT_CONFIGURED"
    assert "EMAIL_PROVIDER" in known.json()["detail"]
    assert _without_request_id(known) == _without_request_id(unknown)
    assert await _outbox(session_factory) == []
    assert await _reset_tokens(session_factory) == []
    # An unconfigured server is not a request to rate-limit.
    assert await _count_throttle_events(session_factory, "password_forgot_email") == 0


async def test_the_fourth_request_for_one_email_in_fifteen_minutes_is_rate_limited(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com")
    queued_at_signup = len(await _outbox(session_factory))

    statuses = [
        (await email_client.post(FORGOT, json={"email": "ada@example.com"})).status_code
        for _ in range(3)
    ]
    fourth = await email_client.post(FORGOT, json={"email": "ada@example.com"})

    assert statuses == [202, 202, 202]
    assert fourth.status_code == 429
    assert fourth.json()["code"] == "RATE_LIMITED"
    assert 0 < int(fourth.headers["Retry-After"]) <= 15 * 60
    assert len(await _outbox(session_factory)) == queued_at_signup + 3  # nothing for the fourth


async def test_the_email_limit_counts_unknown_addresses_too(
    email_client: httpx.AsyncClient,
) -> None:
    # Counted per request, so the limit says nothing about whether an account exists.
    statuses = [
        (await email_client.post(FORGOT, json={"email": "nobody@example.com"})).status_code
        for _ in range(4)
    ]

    assert statuses == [202, 202, 202, 429]


async def test_the_email_limit_ignores_the_case_of_the_address(
    email_client: httpx.AsyncClient,
) -> None:
    variants = ["ada@example.com", "ADA@example.com", "Ada@Example.com", "aDa@EXAMPLE.com"]

    statuses = [
        (await email_client.post(FORGOT, json={"email": email})).status_code for email in variants
    ]

    assert statuses == [202, 202, 202, 429]


async def test_the_eleventh_request_from_one_ip_in_fifteen_minutes_is_rate_limited(
    email_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    # Ten different addresses, so only the IP limit can be what stops the eleventh.
    statuses = [
        (await email_client.post(FORGOT, json={"email": f"user{n}@example.com"})).status_code
        for n in range(10)
    ]
    eleventh = await email_client.post(FORGOT, json={"email": "user10@example.com"})

    assert statuses == [202] * 10
    assert eleventh.status_code == 429
    assert eleventh.json()["code"] == "RATE_LIMITED"
    assert 0 < int(eleventh.headers["Retry-After"]) <= 15 * 60
    assert await _count_throttle_events(session_factory, "password_forgot_ip") == 10
    # The email check ran first and recorded the refused request; the refusal rolled it back.
    assert (
        await _count_throttle_events(session_factory, "password_forgot_email", "user10@example.com")
        == 0
    )


async def test_a_refused_request_is_not_recorded_against_either_limit(
    email_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    # Hammering an address must not extend its own lockout or use up the IP's budget.
    for _ in range(5):
        await email_client.post(FORGOT, json={"email": "ada@example.com"})

    assert await _count_throttle_events(session_factory, "password_forgot_email") == 3
    assert await _count_throttle_events(session_factory, "password_forgot_ip") == 3


async def test_forgot_always_checks_the_email_limit_before_the_ip_limit(
    email_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both checks hold an advisory lock until the transaction ends. One fixed order is what keeps
    # two concurrent requests from each waiting on the lock the other holds.
    scopes: list[str] = []
    real_check = password_reset.check_and_record

    async def recording_check(
        db: AsyncSession, scope: str, key: str, limit: int, window: timedelta
    ) -> float | None:
        scopes.append(scope)
        return await real_check(db, scope, key, limit, window)

    monkeypatch.setattr(password_reset, "check_and_record", recording_check)

    await email_client.post(FORGOT, json={"email": "ada@example.com"})

    assert scopes == ["password_forgot_email", "password_forgot_ip"]


async def test_forgot_limits_do_not_touch_the_login_throttle(
    email_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    for _ in range(4):
        await email_client.post(FORGOT, json={"email": "ada@example.com"})

    async with session_factory() as db:
        attempts = await db.scalar(text("SELECT count(*) FROM login_attempts"))
    assert attempts == 0


async def test_forgot_still_applies_the_origin_check(email_client: httpx.AsyncClient) -> None:
    response = await email_client.post(
        FORGOT, json={"email": "ada@example.com"}, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


@pytest.mark.parametrize("body", [{}, {"email": ""}, {"email": "not-an-email"}, {"email": 5}])
async def test_forgot_validates_the_body(
    email_client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await email_client.post(FORGOT, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_forgot_for_the_demo_user_behaves_like_an_unknown_email(
    email_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    async with session_factory() as db:
        await ensure_demo_workspace(db)
        await db.commit()

    demo = await email_client.post(FORGOT, json={"email": DEMO_USER_EMAIL})
    unknown = await email_client.post(FORGOT, json={"email": "nobody@example.com"})

    assert demo.status_code == unknown.status_code == 202
    assert demo.content == unknown.content
    assert await _outbox(session_factory) == []
    assert await _reset_tokens(session_factory) == []


async def test_a_reset_token_lasts_one_hour(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com")
    before = datetime.now(UTC)

    await email_client.post(FORGOT, json={"email": "ada@example.com"})

    after = datetime.now(UTC)
    (token,) = await _reset_tokens(session_factory)
    assert before + timedelta(hours=1) <= token.expires_at <= after + timedelta(hours=1)


async def test_asking_again_retires_the_earlier_link(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    await email_browser_factory("ada@example.com")
    await email_client.post(FORGOT, json={"email": "ada@example.com"})
    await email_client.post(FORGOT, json={"email": "ada@example.com"})
    first, second = [
        _token_in(row)
        for row in await _outbox(session_factory)
        if row.payload["subject"].startswith("Reset")
    ]

    stale = await email_client.post(RESET, json={"token": first, "password": NEW_PASSWORD})
    fresh = await email_client.post(RESET, json={"token": second, "password": NEW_PASSWORD})

    assert stale.status_code == 404
    assert fresh.status_code == 204


async def test_asking_again_does_not_retire_a_verification_link(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    verify = await _issue(session_factory, browser.user["id"], EmailTokenKind.VERIFY)

    await email_client.post(FORGOT, json={"email": "ada@example.com"})

    confirmed = await email_client.post("/api/v1/auth/email/verify/confirm", json={"token": verify})
    assert confirmed.status_code == 200


# --- reset ----------------------------------------------------------------------------------


async def test_reset_with_a_valid_token_changes_the_password_and_ends_every_session(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    # `email_client` is a second device: it signs in with the password, like a later visit would.
    assert (await _login(email_client, "ada@example.com", OLD_PASSWORD)).status_code == 200
    assert (await email_client.get(ME)).status_code == 200
    raw = await _issue(session_factory, browser.user["id"])

    response = await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert response.status_code == 204
    assert response.content == b""
    assert (await browser.get(ME)).status_code == 401
    assert (await email_client.get(ME)).status_code == 401
    assert (await _login(email_client, "ada@example.com", OLD_PASSWORD)).status_code == 401
    assert (await _login(email_client, "ada@example.com", NEW_PASSWORD)).status_code == 200


async def test_reset_does_not_sign_the_user_in(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    raw = await _issue(session_factory, browser.user["id"])

    response = await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert response.status_code == 204
    assert "set-cookie" not in response.headers
    assert not email_client.cookies
    assert (await email_client.get(ME)).status_code == 401


async def test_reset_proves_the_inbox_and_verifies_the_email(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    assert await _verified_at(session_factory, browser.user["id"]) is None
    raw = await _issue(session_factory, browser.user["id"])

    await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert await _verified_at(session_factory, browser.user["id"]) is not None


async def test_reset_keeps_an_earlier_verification_time(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    await _execute(
        session_factory, "UPDATE users SET email_verified_at = now() - interval '2 days'"
    )
    verified_at = await _verified_at(session_factory, browser.user["id"])
    raw = await _issue(session_factory, browser.user["id"])

    await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert await _verified_at(session_factory, browser.user["id"]) == verified_at


async def test_reset_is_audited_in_each_org_the_user_belongs_to(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada: Browser = await email_browser_factory("ada@example.com")
    bob: Browser = await email_browser_factory("bob@example.com")
    orgs = [
        (await ada.post("/api/v1/orgs", json={"name": name})).json()["id"]
        for name in ("First", "Second")
    ]
    bobs_org = (await bob.post("/api/v1/orgs", json={"name": "Bobs"})).json()["id"]
    raw = await _issue(session_factory, ada.user["id"])

    await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    async with session_factory() as db:
        events = (
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == AuditAction.USER_PASSWORD_RESET.value)
            )
        ).all()
    assert sorted(str(event.org_id) for event in events) == sorted(orgs)
    assert bobs_org not in {str(event.org_id) for event in events}
    for event in events:
        assert event.target_type == "user"
        assert event.target_id == ada.user["id"]
        assert str(event.actor_user_id) == ada.user["id"]
        assert event.metadata_ == {"via": "email", "tokens_revoked": 0}


async def test_reset_for_a_user_in_no_org_writes_no_audit_event(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada: Browser = await email_browser_factory("ada@example.com")
    raw = await _issue(session_factory, ada.user["id"])

    response = await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert response.status_code == 204
    async with session_factory() as db:
        assert (await db.scalars(select(AuditEvent))).all() == []


async def test_reset_leaves_other_users_alone(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada: Browser = await email_browser_factory("ada@example.com")
    bob: Browser = await email_browser_factory("bob@example.com")
    raw = await _issue(session_factory, ada.user["id"])

    await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})

    assert (await bob.get(ME)).status_code == 200
    assert await _verified_at(session_factory, bob.user["id"]) is None
    assert (await _login(email_client, "bob@example.com", OLD_PASSWORD)).status_code == 200


async def test_a_reset_link_works_once(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    raw = await _issue(session_factory, browser.user["id"])
    assert (
        await email_client.post(RESET, json={"token": raw, "password": NEW_PASSWORD})
    ).status_code == 204

    again = await email_client.post(
        RESET, json={"token": raw, "password": "someone elses password"}
    )

    assert again.status_code == 404
    assert again.json()["code"] == "NOT_FOUND"
    assert (await _login(email_client, "ada@example.com", NEW_PASSWORD)).status_code == 200


async def test_a_successful_reset_retires_the_users_other_reset_links(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    used = await _issue(session_factory, browser.user["id"])
    leftover = await _issue(session_factory, browser.user["id"])

    await email_client.post(RESET, json={"token": used, "password": NEW_PASSWORD})
    response = await email_client.post(
        RESET, json={"token": leftover, "password": "yet another one"}
    )

    assert response.status_code == 404
    assert all(token.used_at is not None for token in await _reset_tokens(session_factory))


async def test_a_reset_leaves_a_verification_link_usable(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    reset = await _issue(session_factory, browser.user["id"])
    verify = await _issue(session_factory, browser.user["id"], EmailTokenKind.VERIFY)

    await email_client.post(RESET, json={"token": reset, "password": NEW_PASSWORD})

    confirmed = await email_client.post("/api/v1/auth/email/verify/confirm", json={"token": verify})
    assert confirmed.status_code == 200


async def test_a_verification_token_cannot_reset_a_password(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    verify = await _issue(session_factory, browser.user["id"], EmailTokenKind.VERIFY)

    response = await email_client.post(RESET, json={"token": verify, "password": NEW_PASSWORD})

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert (await _login(email_client, "ada@example.com", OLD_PASSWORD)).status_code == 200
    # The wrong route did not spend it: it still verifies the address.
    confirmed = await email_client.post("/api/v1/auth/email/verify/confirm", json={"token": verify})
    assert confirmed.status_code == 200


async def test_every_rejected_reset_token_gets_the_same_answer(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    used = await _issue(session_factory, browser.user["id"])
    await email_client.post(RESET, json={"token": used, "password": NEW_PASSWORD})
    expired = await _issue(session_factory, browser.user["id"])
    await _execute(
        session_factory,
        f"UPDATE email_tokens SET expires_at = now() - interval '1 second' "
        f"WHERE used_at IS NULL AND kind = 'reset' AND user_id = '{browser.user['id']}'",
    )
    verify = await _issue(session_factory, browser.user["id"], EmailTokenKind.VERIFY)

    answers = []
    for token in ("unknown-token", used, expired, verify):
        response = await email_client.post(RESET, json={"token": token, "password": OLD_PASSWORD})
        answers.append((response.status_code, _without_request_id(response)))

    assert {status for status, _ in answers} == {404}
    assert len(set(map(repr, answers))) == 1  # nothing tells an attacker why a token failed


async def test_an_invalid_password_is_rejected_without_spending_the_link(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    raw = await _issue(session_factory, browser.user["id"])

    short = await email_client.post(RESET, json={"token": raw, "password": "too short"})
    long = await email_client.post(RESET, json={"token": raw, "password": "x" * 257})

    assert short.status_code == long.status_code == 422
    assert short.json()["errors"][0]["field"] == "password"
    # The person can fix the typo and use the same link.
    again = await email_client.post(RESET, json={"token": raw, "password": "x" * 256})
    assert again.status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": "abc"},
        {"password": NEW_PASSWORD},
        {"token": "", "password": NEW_PASSWORD},
        {"token": 5, "password": NEW_PASSWORD},
    ],
)
async def test_reset_validates_the_body(
    email_client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await email_client.post(RESET, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_reset_still_applies_the_origin_check(email_client: httpx.AsyncClient) -> None:
    response = await email_client.post(
        RESET,
        json={"token": "whatever", "password": NEW_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


async def test_concurrent_resets_with_one_link_succeed_exactly_once(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com")
    raw = await _issue(session_factory, browser.user["id"])
    passwords = [f"password number {n} here" for n in range(4)]

    responses = await asyncio.gather(
        *(email_client.post(RESET, json={"token": raw, "password": p}) for p in passwords)
    )

    assert sorted(response.status_code for response in responses) == [204, 404, 404, 404]
    winner = passwords[[r.status_code for r in responses].index(204)]
    assert (await _login(email_client, "ada@example.com", winner)).status_code == 200


async def test_the_emailed_link_resets_the_password_end_to_end(
    email_browser_factory: BrowserFactory,
    email_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    browser: Browser = await email_browser_factory("ada@example.com", "Ada")
    await email_client.post(FORGOT, json={"email": "ada@example.com"})
    (row,) = [
        row for row in await _outbox(session_factory) if row.payload["subject"].startswith("Reset")
    ]

    response = await email_client.post(
        RESET, json={"token": _token_in(row), "password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    assert (await browser.get(ME)).status_code == 401
    assert (await _login(email_client, "ada@example.com", NEW_PASSWORD)).status_code == 200
