"""Two-factor authentication over HTTP: enrol, sign in with a second step, recover, turn off.

Time comes from the `clock` fixture, so a code is valid exactly when the test says. Codes are
computed from the secret the app returned, the way an authenticator app would.
"""

import asyncio
import logging
import re
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.email_tokens import issue_token
from app.auth.login_challenge import issue_challenge
from app.config import Settings
from app.core.security import hash_password
from app.db.models import (
    AuditEvent,
    EmailTokenKind,
    LoginAttempt,
    RecoveryCode,
    Session,
    User,
)
from app.services.credentials import replace_password
from tests.auth.conftest import FakeClock
from tests.conftest import BrowserFactory, _make_client
from tests.helpers import Browser, create_workspace

SessionFactory = async_sessionmaker[AsyncSession]

STATUS = "/api/v1/auth/totp"
SETUP = "/api/v1/auth/totp/setup"
ENABLE = "/api/v1/auth/totp/enable"
DISABLE = "/api/v1/auth/totp/disable"
VERIFY = "/api/v1/auth/totp/verify"
LOGIN = "/api/v1/auth/login"
RESET = "/api/v1/auth/password/reset"
ME = "/api/v1/auth/me"
PASSWORD = "correct horse battery"  # what the browser factory signs users up with
RECOVERY_SHAPE = re.compile(r"^[a-z2-7]{5}-[a-z2-7]{5}$")


# --- helpers ----------------------------------------------------------------------------------


async def enroll(browser: Browser, clock: FakeClock) -> tuple[str, list[str]]:
    """Set up and enable 2FA for a signed-in browser. Returns the secret and recovery codes."""
    setup = await browser.post(SETUP, json={"password": PASSWORD})
    assert setup.status_code == 200, setup.text
    secret: str = setup.json()["secret"]
    enable = await browser.post(ENABLE, json={"code": clock.code_for(secret)})
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["recovery_codes"]


async def password_login(client: httpx.AsyncClient, email: str = "ada@example.com") -> Any:
    return await client.post(LOGIN, json={"email": email, "password": PASSWORD})


async def challenge_for(client: httpx.AsyncClient, email: str = "ada@example.com") -> str:
    """Sign in with the password and return the challenge the server asks the second step for."""
    response = await password_login(client, email)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "totp_required", response.text
    return str(response.json()["challenge"])


def has_session_cookies(client: httpx.AsyncClient) -> bool:
    return bool(client.cookies.get("spl_session")) or bool(client.cookies.get("spl_csrf"))


async def audit_actions(factory: SessionFactory) -> list[str]:
    async with factory() as db:
        return list((await db.scalars(select(AuditEvent.action).order_by(AuditEvent.action))).all())


async def count(factory: SessionFactory, model: Any) -> int:
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def password_hash_of(factory: SessionFactory, email: str) -> str | None:
    async with factory() as db:
        return await db.scalar(select(User.password_hash).where(User.email == email))


async def verify_email(factory: SessionFactory, email: str) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE users SET email_verified_at = now() WHERE email = :email"),
            {"email": email},
        )
        await db.commit()


# --- status, setup and enable ------------------------------------------------------------------


async def test_status_is_off_for_a_new_account(totp_browser_factory: BrowserFactory) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await ada.get(STATUS)

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "enabled_at": None, "recovery_codes_remaining": 0}


async def test_setup_returns_a_secret_and_an_otpauth_url_for_the_account(
    totp_browser_factory: BrowserFactory,
) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await ada.post(SETUP, json={"password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"secret", "otpauth_url"}
    assert re.fullmatch(r"[A-Z2-7]{32}", body["secret"])
    assert body["otpauth_url"].startswith("otpauth://totp/Spanlight:ada%40example.com?")
    assert f"secret={body['secret']}" in body["otpauth_url"]
    assert "issuer=Spanlight" in body["otpauth_url"]
    # The secret and the codes are for the person who asked and for nobody's cache.
    assert response.headers["cache-control"] == "no-store"


async def test_setup_again_replaces_the_pending_secret(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    first = (await ada.post(SETUP, json={"password": PASSWORD})).json()["secret"]
    second = (await ada.post(SETUP, json={"password": PASSWORD})).json()["secret"]

    stale = await ada.post(ENABLE, json={"code": clock.code_for(first)})
    fresh = await ada.post(ENABLE, json={"code": clock.code_for(second)})

    assert first != second
    assert stale.status_code == 422
    assert fresh.status_code == 200


async def test_setup_then_enable_with_a_valid_code_returns_ten_recovery_codes(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret = (await ada.post(SETUP, json={"password": PASSWORD})).json()["secret"]

    response = await ada.post(ENABLE, json={"code": clock.code_for(secret)})

    assert response.status_code == 200
    codes = response.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(RECOVERY_SHAPE.fullmatch(code) for code in codes), codes
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"recovery_codes"}


async def test_status_and_me_report_it_once_enabled(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    assert (await ada.get(ME)).json()["totp_enabled"] is False
    await enroll(ada, clock)

    status = (await ada.get(STATUS)).json()

    assert status["enabled"] is True
    assert status["enabled_at"].startswith("2026-10-08T12:00:10")
    assert status["recovery_codes_remaining"] == 10
    assert set(status) == {"enabled", "enabled_at", "recovery_codes_remaining"}
    assert (await ada.get(ME)).json()["totp_enabled"] is True


async def test_nothing_secret_is_in_the_status_or_me_bodies(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, codes = await enroll(ada, clock)

    for url in (STATUS, ME):
        body = (await ada.get(url)).text
        assert secret not in body
        assert not any(code in body for code in codes)


async def test_enable_with_a_wrong_code_is_422_and_enables_nothing(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret = (await ada.post(SETUP, json={"password": PASSWORD})).json()["secret"]
    right = clock.code_for(secret)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"

    response = await ada.post(ENABLE, json={"code": wrong})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOTP_CODE"
    assert (await ada.get(STATUS)).json()["enabled"] is False


async def test_enable_without_setup_is_422(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await ada.post(ENABLE, json={"code": "123456"})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOTP_CODE"


async def test_setup_when_enabled_is_409(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await ada.post(SETUP, json={"password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["code"] == "TOTP_ALREADY_ENABLED"
    # The secret the person already enrolled was not replaced: its next code still works.
    assert (
        await ada.post(DISABLE, json={"code": clock.code_for(secret, steps_ahead=1)})
    ).status_code == 204


async def test_enable_when_enabled_is_409(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await ada.post(ENABLE, json={"code": clock.code_for(secret, steps_ahead=1)})

    assert response.status_code == 409
    assert response.json()["code"] == "TOTP_ALREADY_ENABLED"


async def test_disable_when_not_enabled_is_409(totp_browser_factory: BrowserFactory) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await ada.post(DISABLE, json={"code": "123456"})

    assert response.status_code == 409
    assert response.json()["code"] == "TOTP_NOT_ENABLED"


async def test_setup_without_credentials_keys_is_409_not_configured(
    browser_factory: BrowserFactory,
) -> None:
    ada = await browser_factory("ada@example.com")

    response = await ada.post(SETUP, json={"password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_CONFIGURED"
    assert "CREDENTIALS_KEYS" in response.json()["detail"]
    assert (await ada.get(STATUS)).json()["enabled"] is False


async def test_enable_with_an_unverified_email_is_409_while_email_is_configured(
    totp_email_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_email_browser_factory("ada@example.com")
    secret = (await ada.post(SETUP, json={"password": PASSWORD})).json()["secret"]

    refused = await ada.post(ENABLE, json={"code": clock.code_for(secret)})
    await verify_email(session_factory, "ada@example.com")
    allowed = await ada.post(ENABLE, json={"code": clock.code_for(secret)})

    assert refused.status_code == 409
    assert refused.json()["code"] == "EMAIL_UNVERIFIED"
    assert allowed.status_code == 200


async def test_enable_with_an_unverified_email_is_fine_when_no_email_is_configured(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    # Nobody can verify an address on such an instance, so the rule cannot apply.
    ada = await totp_browser_factory("ada@example.com")

    await enroll(ada, clock)

    assert (await ada.get(STATUS)).json()["enabled"] is True


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", STATUS, None),
        ("POST", SETUP, None),
        ("POST", ENABLE, {"code": "123456"}),
        ("POST", DISABLE, {"code": "123456"}),
    ],
)
async def test_the_session_routes_need_a_session(
    totp_client: httpx.AsyncClient, method: str, url: str, body: dict[str, str] | None
) -> None:
    response = await totp_client.request(method, url, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("url", "body"), [(SETUP, None), (ENABLE, {"code": "123456"}), (DISABLE, {"code": "123456"})]
)
async def test_the_state_changing_routes_need_the_csrf_token(
    totp_browser_factory: BrowserFactory, url: str, body: dict[str, str] | None
) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await ada.http.post(url, json=body)

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


async def test_the_shared_demo_account_cannot_set_up_or_enable(
    totp_client: httpx.AsyncClient,
) -> None:
    # One shared, anonymous account: if a visitor turned 2FA on, every later visitor would be
    # locked out of the demo.
    started = await totp_client.post("/api/v1/demo/session")
    assert started.status_code == 200, started.text
    demo = Browser(http=totp_client, user=started.json())

    setup = await demo.post(SETUP)
    enable = await demo.post(ENABLE, json={"code": "123456"})

    assert setup.status_code == 403
    assert setup.json()["code"] == "FORBIDDEN"
    assert enable.status_code == 403
    assert enable.json()["code"] == "FORBIDDEN"


# --- disable -----------------------------------------------------------------------------------


async def test_disable_with_a_valid_code_turns_it_off_and_deletes_the_recovery_codes(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await ada.post(DISABLE, json={"code": clock.next_code(secret)})

    assert response.status_code == 204
    assert (await ada.get(STATUS)).json() == {
        "enabled": False,
        "enabled_at": None,
        "recovery_codes_remaining": 0,
    }
    assert await count(session_factory, RecoveryCode) == 0
    # Signing in no longer asks for a second step.
    login = await password_login(totp_client)
    assert login.json()["status"] == "signed_in"


async def test_disable_with_a_recovery_code_works(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    _, codes = await enroll(ada, clock)

    response = await ada.post(DISABLE, json={"code": codes[0]})

    assert response.status_code == 204
    assert (await ada.get(STATUS)).json()["enabled"] is False


async def test_disable_needs_a_valid_code(
    totp_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)

    response = await ada.post(DISABLE, json={"code": "000000"})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TOTP_CODE"
    assert (await ada.get(STATUS)).json()["enabled"] is True
    assert await count(session_factory, RecoveryCode) == 10


async def test_disable_cannot_reuse_the_code_that_enabled(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await ada.post(DISABLE, json={"code": clock.code_for(secret)})

    assert response.status_code == 422


async def test_disable_is_throttled_so_a_stolen_session_cannot_guess_codes(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    statuses = [(await ada.post(DISABLE, json={"code": "000000"})).status_code for _ in range(5)]
    blocked = await ada.post(DISABLE, json={"code": clock.next_code(secret)})

    assert statuses == [422] * 5
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) > 0
    assert (await ada.get(STATUS)).json()["enabled"] is True


# --- audit -------------------------------------------------------------------------------------


async def test_enable_and_disable_are_audited_in_each_org_of_the_user(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    first = await create_workspace(ada, org_name="Acme")
    second = await create_workspace(ada, org_name="Globex")
    secret, _ = await enroll(ada, clock)

    await ada.post(DISABLE, json={"code": clock.next_code(secret)})

    async with session_factory() as db:
        events = (
            await db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("user.totp_%"))
                .order_by(AuditEvent.created_at, AuditEvent.org_id)
            )
        ).all()
    assert sorted((str(event.org_id), event.action) for event in events) == sorted(
        [
            (first.org_id, "user.totp_enable"),
            (second.org_id, "user.totp_enable"),
            (first.org_id, "user.totp_disable"),
            (second.org_id, "user.totp_disable"),
        ]
    )
    for event in events:
        assert str(event.actor_user_id) == ada.user["id"]
        assert event.target_type == "user"
        assert event.target_id == ada.user["id"]


async def test_a_failed_enable_or_disable_writes_no_audit_event(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await create_workspace(ada)
    await ada.post(SETUP, json={"password": PASSWORD})

    await ada.post(ENABLE, json={"code": "000000"})
    await ada.post(DISABLE, json={"code": "000000"})

    assert not [a for a in await audit_actions(session_factory) if a.startswith("user.totp_")]


# --- signing in --------------------------------------------------------------------------------


async def test_login_without_two_factor_still_signs_in(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient
) -> None:
    ada = await totp_browser_factory("ada@example.com")

    response = await password_login(totp_client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "signed_in"
    assert body["user"] == ada.user
    assert set(body) == {"status", "user"}
    assert has_session_cookies(totp_client)


async def test_login_with_two_factor_asks_for_the_second_step_and_sets_no_cookies(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)
    sessions_before = await count(session_factory, Session)

    response = await password_login(totp_client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "totp_required"
    assert set(body) == {"status", "challenge", "expires_at"}
    assert body["challenge"]
    assert body["expires_at"].startswith("2026-10-08T12:05:10")
    assert "set-cookie" not in response.headers
    assert not has_session_cookies(totp_client)
    assert await count(session_factory, Session) == sessions_before
    assert (await totp_client.get(ME)).status_code == 401


async def test_a_wrong_password_never_reaches_the_second_step(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)

    response = await totp_client.post(
        LOGIN, json={"email": "ada@example.com", "password": "not the password"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert "challenge" not in response.text


async def test_verify_with_the_current_code_signs_in_and_sets_the_cookies(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"status": "signed_in", "user": ada.user}
    assert has_session_cookies(totp_client)
    assert (await totp_client.get(ME)).status_code == 200
    async with session_factory() as db:
        attempts = (await db.scalars(select(LoginAttempt).order_by(LoginAttempt.id))).all()
    assert [attempt.succeeded for attempt in attempts] == [True, True]
    assert {attempt.email for attempt in attempts} == {"ada@example.com"}


async def test_the_session_made_by_the_second_step_is_a_normal_one(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    await totp_client.post(VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)})

    sessions = (await Browser(http=totp_client, user={}).get("/api/v1/auth/sessions")).json()
    logout = await totp_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": totp_client.cookies["spl_csrf"]}
    )

    assert len(sessions) == 2  # the one from sign-up and this one
    assert logout.status_code == 204


async def test_totp_code_cannot_be_reused(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    totp_app: Any,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    code = clock.next_code(secret)
    first = await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": code}
    )
    assert first.status_code == 200

    async with _make_client(totp_app) as other:
        replay = await other.post(
            VERIFY, json={"challenge": await challenge_for(other), "code": code}
        )

    assert replay.status_code == 401
    assert replay.json()["code"] == "INVALID_TOTP_CODE"
    assert not has_session_cookies(other)


async def test_the_code_used_to_enable_cannot_sign_in(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": clock.code_for(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOTP_CODE"


async def test_a_code_one_step_old_is_accepted_for_clock_drift(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    clock.advance(seconds=60)  # two steps after the enrolment code
    one_step_old = clock.code_for(secret, steps_ahead=-1)

    response = await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": one_step_old}
    )

    assert response.status_code == 200


async def test_a_code_two_steps_old_is_refused(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    clock.advance(seconds=120)
    too_old = clock.code_for(secret, steps_ahead=-2)

    response = await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": too_old}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOTP_CODE"


async def test_the_code_may_be_typed_with_a_space(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    code = clock.next_code(secret)

    response = await totp_client.post(
        VERIFY,
        json={"challenge": await challenge_for(totp_client), "code": f"{code[:3]} {code[3:]}"},
    )

    assert response.status_code == 200


async def test_a_recovery_code_signs_in_and_works_once(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    totp_app: Any,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    _, codes = await enroll(ada, clock)

    first = await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": codes[0]}
    )
    assert first.status_code == 200
    assert (await totp_client.get(STATUS)).json()["recovery_codes_remaining"] == 9

    async with _make_client(totp_app) as other:
        second = await other.post(
            VERIFY, json={"challenge": await challenge_for(other), "code": codes[0]}
        )
        # Another code from the same set still works.
        third = await other.post(
            VERIFY, json={"challenge": await challenge_for(other), "code": codes[1]}
        )

    assert second.status_code == 401
    assert second.json()["code"] == "INVALID_TOTP_CODE"
    assert third.status_code == 200


async def test_two_verifies_of_the_same_code_at_once_let_exactly_one_in(
    totp_browser_factory: BrowserFactory, totp_app: Any, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    code = clock.next_code(secret)

    async def attempt() -> httpx.Response:
        async with _make_client(totp_app) as client:
            challenge = await challenge_for(client)
            return await client.post(VERIFY, json={"challenge": challenge, "code": code})

    # Both challenges exist before either verify runs.
    results = await asyncio.gather(attempt(), attempt(), attempt())

    assert sorted(response.status_code for response in results) == [200, 401, 401]


# --- the challenge -----------------------------------------------------------------------------


async def test_an_expired_challenge_is_401(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    clock.advance(minutes=5)

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.code_for(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"
    assert not has_session_cookies(totp_client)


async def test_a_challenge_that_is_still_inside_its_five_minutes_works(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    clock.advance(minutes=4, seconds=50)

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.code_for(secret)}
    )

    assert response.status_code == 200


@pytest.mark.parametrize("damage", ["flip_payload", "flip_signature", "truncate", "empty", "junk"])
async def test_a_tampered_challenge_is_401(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    clock: FakeClock,
    damage: str,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    payload, signature = challenge.split(".")
    tampered = {
        "flip_payload": f"{payload[:-2]}{'AA' if payload[-2:] != 'AA' else 'BB'}.{signature}",
        "flip_signature": f"{payload}.{signature[:-2]}{'AA' if signature[-2:] != 'AA' else 'BB'}",
        "truncate": challenge[:-5],
        "empty": "",
        "junk": "not-a-challenge",
    }[damage]

    response = await totp_client.post(
        VERIFY, json={"challenge": tampered, "code": clock.next_code(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"


async def test_a_challenge_signed_with_another_key_is_401(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    settings: Settings,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    forged = issue_challenge(
        settings.model_copy(update={"secret_key": SecretStr("someone-elses-key")}),
        uuid.UUID(ada.user["id"]),
        clock(),
        password_hash=await password_hash_of(session_factory, "ada@example.com"),
    )

    response = await totp_client.post(
        VERIFY, json={"challenge": forged.token, "code": clock.next_code(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"


async def test_a_challenge_for_a_user_who_does_not_exist_is_401(
    totp_client: httpx.AsyncClient, totp_settings: Settings, clock: FakeClock
) -> None:
    ghost = issue_challenge(totp_settings, uuid.uuid4(), clock(), password_hash=None)

    response = await totp_client.post(VERIFY, json={"challenge": ghost.token, "code": "123456"})

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"


async def test_a_challenge_for_a_user_without_two_factor_never_signs_anyone_in(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    totp_settings: Settings,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    bob = await totp_browser_factory("bob@example.com")
    challenge = issue_challenge(
        totp_settings,
        uuid.UUID(bob.user["id"]),
        clock(),
        password_hash=await password_hash_of(session_factory, "bob@example.com"),
    )

    response = await totp_client.post(VERIFY, json={"challenge": challenge.token, "code": "123456"})

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"
    assert not has_session_cookies(totp_client)


async def test_a_challenge_stops_working_once_two_factor_is_turned_off(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, codes = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    await ada.post(DISABLE, json={"code": codes[0]})

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"


async def test_another_users_code_does_not_sign_in_through_my_challenge(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    bob = await totp_browser_factory("bob@example.com")
    await enroll(ada, clock)
    bob_secret, _ = await enroll(bob, clock)
    ada_challenge = await challenge_for(totp_client, "ada@example.com")

    response = await totp_client.post(
        VERIFY, json={"challenge": ada_challenge, "code": clock.next_code(bob_secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOTP_CODE"
    assert not has_session_cookies(totp_client)


@pytest.mark.parametrize("body", [{}, {"challenge": "x"}, {"code": "123456"}, {"challenge": 1}])
async def test_verify_validates_its_body(
    totp_client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await totp_client.post(VERIFY, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_verify_needs_an_allowed_origin(totp_app: Any) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=totp_app), base_url="http://testserver"
    ) as no_origin:
        response = await no_origin.post(VERIFY, json={"challenge": "x", "code": "123456"})

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


# --- a challenge does not outlive a password change --------------------------------------------


async def reset_password_by_email_link(
    factory: SessionFactory, client: httpx.AsyncClient, user_id: str, new_password: str
) -> None:
    """The real reset flow: an emailed token, then `POST /auth/password/reset`."""
    async with factory() as db:
        token = await issue_token(db, uuid.UUID(user_id), EmailTokenKind.RESET, timedelta(hours=1))
        await db.commit()
    response = await client.post(RESET, json={"token": token, "password": new_password})
    assert response.status_code == 204, response.text


async def test_a_challenge_dies_when_the_password_is_reset_before_the_second_step(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    # The password may be exactly what the owner is replacing because it leaked. A challenge
    # issued for the old one must not turn into a session afterwards.
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    await reset_password_by_email_link(
        session_factory, totp_client, ada.user["id"], "a brand new passphrase"
    )

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"
    assert not has_session_cookies(totp_client)
    assert await count(session_factory, Session) == 0  # the reset ended every session
    # The refused code is not spent and not counted against the account.
    async with session_factory() as db:
        failures = await db.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))
        )
    assert failures == 0


async def test_signing_in_again_with_the_new_password_works(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    await reset_password_by_email_link(
        session_factory, totp_client, ada.user["id"], "a brand new passphrase"
    )

    login = await totp_client.post(
        LOGIN, json={"email": "ada@example.com", "password": "a brand new passphrase"}
    )
    verify = await totp_client.post(
        VERIFY, json={"challenge": login.json()["challenge"], "code": clock.next_code(secret)}
    )

    assert login.json()["status"] == "totp_required"
    assert verify.status_code == 200, verify.text


async def test_a_challenge_dies_on_any_password_change_not_only_a_reset(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    async with session_factory() as db:
        user = (await db.scalars(select(User))).one()
        await replace_password(db, user, hash_password("another passphrase"))
        await db.commit()

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "TOTP_CHALLENGE_INVALID"


async def test_an_unrelated_change_to_the_account_leaves_the_challenge_alone(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    await verify_email(session_factory, "ada@example.com")  # touches the row, not the password

    response = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert response.status_code == 200, response.text


# --- throttling --------------------------------------------------------------------------------


async def test_wrong_codes_count_as_failed_logins_and_the_sixth_attempt_is_429(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)

    wrong = [
        await totp_client.post(VERIFY, json={"challenge": challenge, "code": "000000"})
        for _ in range(5)
    ]
    blocked = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert [response.status_code for response in wrong] == [401] * 5
    assert {response.json()["code"] for response in wrong} == {"INVALID_TOTP_CODE"}
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    async with session_factory() as db:
        failures = await db.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))
        )
    assert failures == 5
    assert not has_session_cookies(totp_client)


async def test_wrong_codes_and_wrong_passwords_share_one_counter(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    for _ in range(3):
        await totp_client.post(LOGIN, json={"email": "ada@example.com", "password": "wrong wrong"})
    for _ in range(2):
        await totp_client.post(VERIFY, json={"challenge": challenge, "code": "000000"})

    password = await password_login(totp_client)
    code = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )

    assert password.status_code == 429
    assert code.status_code == 429


async def test_a_throttled_verify_does_not_say_whether_the_code_was_right(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    for _ in range(5):
        await totp_client.post(VERIFY, json={"challenge": challenge, "code": "000000"})

    right = await totp_client.post(
        VERIFY, json={"challenge": challenge, "code": clock.next_code(secret)}
    )
    wrong = await totp_client.post(VERIFY, json={"challenge": challenge, "code": "000000"})

    assert right.status_code == wrong.status_code == 429
    assert right.json()["detail"] == wrong.json()["detail"]


async def test_a_successful_second_step_does_not_count_toward_the_limit(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    for _ in range(7):
        response = await totp_client.post(
            VERIFY,
            json={"challenge": await challenge_for(totp_client), "code": clock.next_code(secret)},
        )
        assert response.status_code == 200


async def test_users_who_are_in_no_org_still_sign_in_with_two_factor(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    # Guards the audit helper: a user with no org leaves no audit event and must not break.
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await totp_client.post(
        VERIFY,
        json={"challenge": await challenge_for(totp_client), "code": clock.next_code(secret)},
    )

    assert response.status_code == 200


async def test_the_user_row_is_not_changed_by_a_failed_verify(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)
    async with session_factory() as db:
        before = (await db.scalars(select(User))).one().totp_last_step

    await totp_client.post(
        VERIFY, json={"challenge": await challenge_for(totp_client), "code": "000000"}
    )

    async with session_factory() as db:
        assert (await db.scalars(select(User))).one().totp_last_step == before


async def test_no_secret_code_or_challenge_is_ever_logged(
    totp_browser_factory: BrowserFactory,
    totp_client: httpx.AsyncClient,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ada = await totp_browser_factory("ada@example.com")
    secret, recovery_codes = await enroll(ada, clock)
    challenge = await challenge_for(totp_client)
    wrong_code = "000000"
    right_code = clock.next_code(secret)
    await totp_client.post(VERIFY, json={"challenge": challenge, "code": wrong_code})
    await totp_client.post(VERIFY, json={"challenge": challenge, "code": right_code})
    await ada.post(DISABLE, json={"code": recovery_codes[0]})

    logged = caplog.text

    assert "totp_enabled" in logged  # the events are logged...
    assert "totp_disabled" in logged
    assert "totp_code_rejected" in logged
    for secret_value in (secret, challenge, right_code, *recovery_codes):
        assert secret_value not in logged  # ...without anything that would let someone in
        assert secret_value.replace("-", "") not in logged
