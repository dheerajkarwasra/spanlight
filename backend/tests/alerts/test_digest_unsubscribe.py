"""Opting out of the weekly digest: per user, for every project.

A user turns it off in Settings > Account (`PATCH /auth/me`) or with the link in a digest. Every
digest carries RFC 8058 one-click headers whose URL holds a token for its recipient alone; a mail
client POSTs to it with no cookie, no CSRF token and no Origin. Any token, valid or not, gets the
same 200, so the route cannot be used to tell valid tokens from forged ones.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.digest_job import digest_week, send_digests
from app.alerts.unsubscribe import unsubscribe_token, user_for_unsubscribe_token
from app.config import Settings
from app.db.models import NotificationOutbox, User
from app.db.rls import bypass_rls
from tests.conftest import BrowserFactory
from tests.helpers import create_workspace, join_with_role, verify_email

SessionFactory = async_sessionmaker[AsyncSession]

ME = "/api/v1/auth/me"
UNSUBSCRIBE = "/api/v1/email/unsubscribe"
ONE_CLICK = {"content": b"List-Unsubscribe=One-Click"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)  # a Monday


async def _opted_out(factory: SessionFactory, user_id: str) -> bool:
    async with factory() as db:
        value = await db.scalar(
            select(User.weekly_digest_opt_out).where(User.id == uuid.UUID(user_id))
        )
        assert value is not None
        return value


async def _set_opt_out(factory: SessionFactory, email: str, value: bool) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE users SET weekly_digest_opt_out = :value WHERE email = :email"),
            {"value": value, "email": email},
        )
        await db.commit()


# --- tokens ----------------------------------------------------------------------------------


def test_token_names_its_user_and_nothing_else_validates(settings: Settings) -> None:
    user_id = uuid.uuid4()
    token = unsubscribe_token(user_id, settings)

    assert user_for_unsubscribe_token(token, settings) == user_id
    # Another user's id with this signature, a changed byte, another key, junk.
    other = f"{uuid.uuid4().hex}.{token.split('.', 1)[1]}"
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    rekeyed = settings.model_copy(
        update={"secret_key": SecretStr("another-secret-key-of-32-characters!")}
    )
    for forged in (other, flipped, "", "abc", "not-a-uuid.sig", token + ".x", "x" * 10_000):
        assert user_for_unsubscribe_token(forged, settings) is None
    assert user_for_unsubscribe_token(token, rekeyed) is None


# --- the digest job --------------------------------------------------------------------------


async def test_digest_skips_opted_out_users_and_carries_one_click_headers(
    browser_factory: BrowserFactory, session_factory: SessionFactory, settings: Settings
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    grace = await browser_factory("grace@example.com", "Grace")
    linus = await browser_factory("linus@example.com", "Linus")
    workspace = await create_workspace(ada)
    await join_with_role(ada, grace, workspace.org_id, "member")
    await join_with_role(ada, linus, workspace.org_id, "member")
    for address in ("ada@example.com", "grace@example.com", "linus@example.com"):
        await verify_email(session_factory, address)
    await _set_opt_out(session_factory, "grace@example.com", True)
    week = digest_week(NOW)
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO span_rollups_hourly (project_id, bucket_start, kind, span_count, "
                "errors, input_tokens, output_tokens, cached_tokens, unpriced_calls, "
                "latency_buckets, ttft_buckets) VALUES (:project, :at, 'llm', 1, 0, 1, 1, 0, 1, "
                "array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32]))"
            ),
            {"project": workspace.project_id, "at": week.start + timedelta(hours=1)},
        )
        await db.commit()

    run = await send_digests(session_factory, settings, now=NOW)

    assert run.rows == 2
    async with session_factory() as db:
        rows = (await db.scalars(select(NotificationOutbox))).all()
    by_address = {row.target["to"]: row.payload for row in rows}
    assert sorted(by_address) == ["ada@example.com", "linus@example.com"]
    for browser in (ada, linus):
        payload: dict[str, Any] = by_address[browser.user["email"]]
        token = unsubscribe_token(uuid.UUID(browser.user["id"]), settings)
        assert payload["headers"] == {
            "List-Unsubscribe": f"<{settings.app_base_url}{UNSUBSCRIBE}?token={token}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        footer = f"{settings.app_base_url}/unsubscribe#token={token}"
        assert footer in payload["text"] and footer in payload["html"]


# --- the one-click route ---------------------------------------------------------------------


async def test_one_click_post_without_origin_or_cookies_opts_out(
    browser_factory: BrowserFactory,
    bare_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    grace = await browser_factory("grace@example.com", "Grace")
    token = unsubscribe_token(uuid.UUID(ada.user["id"]), settings)

    response = await bare_client.post(
        UNSUBSCRIBE, params={"token": token}, headers=FORM, **ONE_CLICK
    )

    assert response.status_code == 200, response.text
    assert await _opted_out(session_factory, ada.user["id"])
    assert not await _opted_out(session_factory, grace.user["id"])
    # Repeating it (mail clients retry) changes nothing and answers the same.
    again = await bare_client.post(UNSUBSCRIBE, params={"token": token}, headers=FORM, **ONE_CLICK)
    assert (again.status_code, again.json()) == (200, response.json())


async def test_forged_or_malformed_tokens_answer_the_same_and_change_nothing(
    browser_factory: BrowserFactory,
    bare_client: httpx.AsyncClient,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    valid = unsubscribe_token(uuid.uuid4(), settings)  # a real signature for a user who is not here
    forged = f"{ada.user['id'].replace('-', '')}.{valid.split('.', 1)[1]}"
    expected = None
    for token in (forged, valid, "garbage", ""):
        response = await bare_client.post(
            UNSUBSCRIBE, params={"token": token}, headers=FORM, **ONE_CLICK
        )
        assert response.status_code == 200, response.text
        expected = expected or response.json()
        assert response.json() == expected
    no_token = await bare_client.post(UNSUBSCRIBE, headers=FORM, **ONE_CLICK)
    assert (no_token.status_code, no_token.json()) == (200, expected)
    assert not await _opted_out(session_factory, ada.user["id"])


async def test_page_posts_the_fragment_token_as_json(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    token = unsubscribe_token(uuid.UUID(ada.user["id"]), settings)

    response = await client.post(UNSUBSCRIBE, json={"token": token})

    assert response.status_code == 200, response.text
    assert await _opted_out(session_factory, ada.user["id"])


# --- Settings > Account ----------------------------------------------------------------------


async def test_patch_me_turns_the_digest_off_and_on(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com", "Ada")
    assert (await ada.get(ME)).json()["weekly_digest"] is True

    off = await ada.patch(ME, json={"weekly_digest": False})

    assert off.status_code == 200, off.text
    assert off.json()["name"] == "Ada"
    assert await _opted_out(session_factory, ada.user["id"])
    assert (await ada.get(ME)).json()["weekly_digest"] is False

    assert (await ada.patch(ME, json={"weekly_digest": True})).status_code == 200
    assert (await ada.get(ME)).json()["weekly_digest"] is True
    assert (await ada.patch(ME, json={"weekly_digest": "nope"})).status_code == 422


async def test_patch_me_digest_refuses_the_demo_user(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/demo/session")).status_code == 200
    csrf = {"X-CSRF-Token": client.cookies["spl_csrf"]}

    response = await client.patch(ME, json={"weekly_digest": False}, headers=csrf)

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
