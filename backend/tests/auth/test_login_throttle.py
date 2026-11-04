"""The sign-in failure limits: 5 per (email, IP), 50 per email, 20 per IP, and exact under load.

Five failures from one address lock that address out of one account, not the account itself:
a stranger who knows an email cannot lock its owner out with five requests. The email-only cap
of fifty still stops a distributed guess. The count and the insert happen under one advisory
lock per email, so parallel guesses never record more failures than the limit allows.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import auth as auth_routes
from app.auth.login_throttle import (
    MAX_FAILURES_PER_EMAIL,
    MAX_FAILURES_PER_EMAIL_IP,
    MAX_HASHES_PER_EMAIL_IP,
)
from tests.conftest import TEST_ORIGIN

SessionFactory = async_sessionmaker[AsyncSession]
LOGIN = "/api/v1/auth/login"
EMAIL = "ada@example.com"


def _client_from(app: Any, ip: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 40000)),
        base_url=TEST_ORIGIN,
        headers={"Origin": TEST_ORIGIN},
    )


async def _fail(client: httpx.AsyncClient, email: str = EMAIL) -> int:
    response = await client.post(LOGIN, json={"email": email, "password": "wrong password!"})
    return response.status_code


async def _failure_rows(factory: SessionFactory) -> int:
    async with factory() as db:
        count = await db.scalar(
            text("SELECT count(*) FROM login_attempts WHERE email = :email AND NOT succeeded"),
            {"email": EMAIL},
        )
    return int(count or 0)


async def test_five_failures_from_one_ip_block_that_ip_for_the_email(
    app: Any, browser_factory: Any
) -> None:
    await browser_factory(EMAIL)
    async with (
        _client_from(app, "198.51.100.10") as attacker,
        _client_from(app, "198.51.100.20") as ada,
    ):
        for _ in range(MAX_FAILURES_PER_EMAIL_IP):
            assert await _fail(attacker) == 401
        assert await _fail(attacker) == 429

        # The account's owner, on another network, is not locked out by the stranger's guesses.
        assert await _fail(ada) == 401


async def test_failures_from_other_ips_do_not_lock_the_email_until_fifty(
    app: Any, browser_factory: Any, session_factory: SessionFactory
) -> None:
    await browser_factory(EMAIL)
    now = datetime.now(UTC)
    # 49 failures spread over many addresses: under every per-IP limit, one short of the cap.
    rows = [
        {"email": EMAIL, "ip": f"198.51.100.{index // 4}", "created_at": now}
        for index in range(MAX_FAILURES_PER_EMAIL - 1)
    ]
    async with session_factory() as db:
        await db.execute(
            text(
                "INSERT INTO login_attempts (email, ip, succeeded, created_at) "
                "VALUES (:email, :ip, false, :created_at)"
            ),
            rows,
        )
        await db.commit()

    async with (
        _client_from(app, "203.0.113.1") as fiftieth,
        _client_from(app, "203.0.113.2") as next_ip,
    ):
        assert await _fail(fiftieth) == 401
        assert await _fail(next_ip) == 429


@pytest.mark.serial
async def test_parallel_wrong_passwords_never_exceed_five_verifies(
    app: Any, browser_factory: Any, session_factory: SessionFactory
) -> None:
    await browser_factory(EMAIL)
    async with _client_from(app, "198.51.100.10") as attacker:
        statuses = await asyncio.gather(*(_fail(attacker) for _ in range(20)))

    recorded = await _failure_rows(session_factory)
    assert recorded <= MAX_FAILURES_PER_EMAIL_IP
    assert statuses.count(401) == recorded
    assert statuses.count(429) == 20 - recorded


@pytest.mark.serial
async def test_parallel_guesses_with_mixed_case_spellings_share_one_lock(
    app: Any, browser_factory: Any, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`email` is CITEXT: every spelling counts against one account, so all must take one lock."""
    await browser_factory(EMAIL)

    async def wrong_at_once(*args: Any, **kwargs: Any) -> bool:
        # No hashing: every guess reaches the count and the insert at the same moment, which is
        # the race the lock exists for.
        return False

    monkeypatch.setattr(auth_routes, "verify_password_async", wrong_at_once)
    spellings = ["ada@example.com", "Ada@example.com", "ADA@example.com", "aDa@Example.com"]
    async with _client_from(app, "198.51.100.10") as attacker:
        statuses = await asyncio.gather(
            *(_fail(attacker, spellings[index % 4]) for index in range(20))
        )

    recorded = await _failure_rows(session_factory)
    assert recorded <= MAX_FAILURES_PER_EMAIL_IP
    assert statuses.count(401) == recorded


@pytest.mark.serial
async def test_a_burst_runs_the_password_hash_at_most_twenty_times(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guesses that arrive before any failure is recorded all pass the failure count; the hash
    budget per (email, IP) still bounds the argon2 work they cause."""
    await browser_factory(EMAIL)
    real_verify = auth_routes.verify_password_async
    verifies = 0

    async def counting_verify(*args: Any, **kwargs: Any) -> bool:
        nonlocal verifies
        verifies += 1
        return await real_verify(*args, **kwargs)

    monkeypatch.setattr(auth_routes, "verify_password_async", counting_verify)

    async with _client_from(app, "198.51.100.10") as attacker:
        statuses = await asyncio.gather(*(_fail(attacker) for _ in range(30)))

    assert verifies <= MAX_HASHES_PER_EMAIL_IP
    assert statuses.count(429) >= 30 - MAX_HASHES_PER_EMAIL_IP
    assert set(statuses) <= {401, 429}
