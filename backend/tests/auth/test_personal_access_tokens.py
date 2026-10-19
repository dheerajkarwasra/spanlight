"""Personal access tokens: create, list and revoke them, and what a token may do as its user.

A token acts as the user who made it, with the user's memberships read afresh on every request
and never more than its own scope allows. Only a signed-in session manages tokens.
"""

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import PERMISSION_CLASSES, Permission, permission_class
from tests.auth.conftest import FakeClock
from tests.auth.test_totp_routes import enroll
from tests.conftest import BrowserFactory
from tests.helpers import (
    Browser,
    bearer,
    create_token,
    create_workspace,
    expire_token,
    join_with_role,
    new_trace_id,
    span,
)

SessionFactory = async_sessionmaker[AsyncSession]

TOKENS = "/api/v1/auth/tokens"
ME = "/api/v1/auth/me"
TOKEN_SHAPE = re.compile(r"^spl_pat_[a-z2-7]{12}_[a-z2-7]{32}$")
LIST_FIELDS = {"id", "name", "prefix", "scope", "created_at", "expires_at", "last_used_at"}


def secret_of(token: str) -> str:
    return token.rsplit("_", 1)[1]


async def row(factory: SessionFactory, token_id: str, column: str) -> Any:
    async with factory() as db:
        return (
            await db.execute(
                text(f"SELECT {column} FROM personal_access_tokens WHERE id = :id"),
                {"id": token_id},
            )
        ).scalar()


# --- creating ----------------------------------------------------------------------------------


async def test_creating_a_token_returns_the_secret_in_the_201(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")

    response = await ada.post(TOKENS, json={"name": "laptop", "scope": "read"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == LIST_FIELDS | {"token"}
    assert TOKEN_SHAPE.match(body["token"])
    assert body["token"].startswith(body["prefix"] + "_")
    assert body["name"] == "laptop"
    assert body["scope"] == "read"
    assert body["expires_at"] is None
    assert body["last_used_at"] is None
    assert response.headers["cache-control"] == "no-store"


async def test_the_secret_is_never_shown_again(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)
    secret = secret_of(created["token"])

    listed = await ada.get(TOKENS)
    me = await ada.get(ME)

    assert listed.status_code == 200
    for response in (listed, me):
        assert secret not in response.text
        assert created["token"] not in response.text
    (item,) = listed.json()
    assert set(item) == LIST_FIELDS
    assert item["id"] == created["id"]
    assert item["prefix"] == created["prefix"]


async def test_only_a_digest_of_the_secret_is_stored(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)

    stored = await row(session_factory, created["id"], "secret_hash")

    assert bytes(stored) == hashlib.sha256(secret_of(created["token"]).encode()).digest()
    async with session_factory() as db:
        dump = (await db.execute(text("SELECT t::text FROM personal_access_tokens t"))).scalar_one()
    assert secret_of(created["token"]) not in dump


async def test_two_tokens_get_different_prefixes_and_secrets(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")

    first, second = await create_token(ada), await create_token(ada)

    assert first["id"] != second["id"]
    assert first["prefix"] != second["prefix"]
    assert first["token"] != second["token"]


async def test_an_expiry_is_kept_and_a_time_without_an_offset_is_utc(
    browser_factory: Any,
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    when = datetime.now(UTC).replace(microsecond=0) + timedelta(days=90)

    with_offset = await create_token(ada, expires_at=when.isoformat())
    naive = await create_token(ada, expires_at=when.replace(tzinfo=None).isoformat())

    for created in (with_offset, naive):
        assert datetime.fromisoformat(created["expires_at"]) == when


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "cli"},  # a scope is a decision: there is no default
        {"scope": "write"},
        {"name": "", "scope": "read"},
        {"name": "   ", "scope": "read"},
        {"name": "x" * 101, "scope": "read"},
        {"name": "cli", "scope": "admin"},
        {"name": "cli", "scope": "READ"},
        {"name": "cli", "scope": None},
        {"name": "cli", "scope": ["read"]},
        {"name": "cli", "scope": "read", "expires_at": "2020-01-01T00:00:00Z"},
        {"name": "cli", "scope": "read", "expires_at": "not a time"},
    ],
)
async def test_an_invalid_request_is_422_and_creates_nothing(
    browser_factory: Any, body: dict[str, Any]
) -> None:
    ada: Browser = await browser_factory("ada@example.com")

    response = await ada.post(TOKENS, json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert (await ada.get(TOKENS)).json() == []


async def test_the_demo_account_cannot_create_tokens(client: httpx.AsyncClient) -> None:
    # Every visitor is the same user, so a token would outlive the visit and belong to whoever
    # saw it.
    assert (await client.post("/api/v1/demo/session")).status_code == 200
    csrf = {"X-CSRF-Token": client.cookies["spl_csrf"]}

    response = await client.post(TOKENS, json={"name": "cli", "scope": "read"}, headers=csrf)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert (await client.get(TOKENS)).json() == []


# --- listing -----------------------------------------------------------------------------------


async def test_the_list_holds_only_your_active_tokens_newest_first(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    grace: Browser = await browser_factory("grace@example.com")
    old = await create_token(ada, name="old")
    revoked = await create_token(ada, name="revoked")
    expired = await create_token(ada, name="expired", expires_at="2099-01-01T00:00:00Z")
    new = await create_token(ada, name="new")
    await create_token(grace, name="graces")
    assert (await ada.delete(f"{TOKENS}/{revoked['id']}")).status_code == 204
    await expire_token(session_factory, expired["id"])

    listed = (await ada.get(TOKENS)).json()

    assert [item["name"] for item in listed] == ["new", "old"]
    assert [item["id"] for item in listed] == [new["id"], old["id"]]
    assert (await grace.get(TOKENS)).json()[0]["name"] == "graces"


async def test_listing_needs_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get(TOKENS)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# --- revoking ----------------------------------------------------------------------------------


async def test_a_revoked_token_stops_working_and_leaves_the_list(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)
    assert (await bare_client.get(ME, headers=bearer(created["token"]))).status_code == 200

    revoke = await ada.delete(f"{TOKENS}/{created['id']}")

    assert revoke.status_code == 204
    assert revoke.content == b""
    assert (await ada.get(TOKENS)).json() == []
    after = await bare_client.get(ME, headers=bearer(created["token"]))
    assert after.status_code == 401
    assert after.json()["code"] == "UNAUTHORIZED"


async def test_revoking_twice_is_a_204_and_keeps_the_first_time(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)
    await ada.delete(f"{TOKENS}/{created['id']}")
    first = await row(session_factory, created["id"], "revoked_at")

    again = await ada.delete(f"{TOKENS}/{created['id']}")

    assert again.status_code == 204
    assert first is not None
    assert await row(session_factory, created["id"], "revoked_at") == first


async def test_revoking_one_token_leaves_the_others(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    keep, drop = await create_token(ada), await create_token(ada)

    await ada.delete(f"{TOKENS}/{drop['id']}")

    assert (await bare_client.get(ME, headers=bearer(keep["token"]))).status_code == 200
    assert (await bare_client.get(ME, headers=bearer(drop["token"]))).status_code == 401


async def test_someone_elses_token_is_a_404_and_stays_valid(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    mallory: Browser = await browser_factory("mallory@example.com")
    created = await create_token(ada)

    response = await mallory.delete(f"{TOKENS}/{created['id']}")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert (await bare_client.get(ME, headers=bearer(created["token"]))).status_code == 200
    assert len((await ada.get(TOKENS)).json()) == 1


async def test_an_unknown_token_is_a_404(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")

    response = await ada.delete(f"{TOKENS}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert (await ada.delete(f"{TOKENS}/not-a-uuid")).status_code == 422


async def test_creating_and_revoking_need_the_csrf_header(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)

    create = await ada.http.post(TOKENS, json={"name": "cli", "scope": "read"})
    revoke = await ada.http.delete(f"{TOKENS}/{created['id']}")

    for response in (create, revoke):
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_FAILED"
    assert len((await ada.get(TOKENS)).json()) == 1


# --- logs --------------------------------------------------------------------------------------


def logged(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    # Structlog hands the stdlib handler the event dict itself as the record's message.
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == event
    ]


async def test_creating_and_revoking_are_logged_without_the_secret(
    browser_factory: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ada: Browser = await browser_factory("ada@example.com")

    created = await create_token(ada, scope="read")
    await ada.delete(f"{TOKENS}/{created['id']}")
    await ada.delete(f"{TOKENS}/{created['id']}")  # already revoked: nothing new to say

    (made,) = logged(caplog, "pat_created")
    (ended,) = logged(caplog, "pat_revoked")
    assert made["user_id"] == ada.user["id"]
    assert made["token_id"] == created["id"]
    assert made["scope"] == "read"
    assert ended["user_id"] == ada.user["id"]
    assert ended["token_id"] == created["id"]
    assert secret_of(created["token"]) not in caplog.text


async def test_using_a_token_never_logs_it(
    browser_factory: Any, bare_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ada: Browser = await browser_factory("ada@example.com")
    created = await create_token(ada)

    await bare_client.get(ME, headers=bearer(created["token"]))
    await bare_client.get(ME, headers=bearer(created["token"][:-1] + "a"))

    assert secret_of(created["token"]) not in caplog.text


# --- using a token -----------------------------------------------------------------------------


async def test_a_token_lists_the_traces_of_a_project_its_user_belongs_to(
    browser_factory: Any, client: httpx.AsyncClient, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    trace_id = new_trace_id()
    ingested = await client.post(
        "/v1/traces", json={"spans": [span(trace_id=trace_id)]}, headers=bearer(workspace.api_key)
    )
    assert ingested.status_code == 200
    token = await create_token(ada, scope="read")

    response = await bare_client.get(
        f"/api/v1/projects/{workspace.project_id}/traces", headers=bearer(token["token"])
    )

    assert response.status_code == 200, response.text
    # Row-level security is bound to the project, so the rows come through.
    assert [item["trace_id"] for item in response.json()["items"]] == [trace_id]


async def test_a_token_gets_404_for_a_project_its_user_is_not_a_member_of(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    mallory: Browser = await browser_factory("mallory@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(mallory, scope="write")

    for path in (
        f"/api/v1/projects/{workspace.project_id}/traces",
        f"/api/v1/projects/{workspace.project_id}",
        f"/api/v1/orgs/{workspace.org_id}",
        f"/api/v1/orgs/{workspace.org_id}/members",
    ):
        response = await bare_client.get(path, headers=bearer(token["token"]))
        assert response.status_code == 404, path
        assert response.json()["code"] == "NOT_FOUND"
    # Writing to it is the same 404, not a hint that the org exists.
    created = await bare_client.post(
        f"/api/v1/orgs/{workspace.org_id}/projects",
        json={"name": "Mine now"},
        headers=bearer(token["token"]),
    )
    assert created.status_code == 404


async def test_a_read_token_gets_404_not_a_scope_error_outside_its_orgs(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    mallory: Browser = await browser_factory("mallory@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(mallory, scope="read")

    response = await bare_client.post(
        f"/api/v1/orgs/{workspace.org_id}/projects",
        json={"name": "Mine now"},
        headers=bearer(token["token"]),
    )

    # TOKEN_SCOPE here would tell a stranger that the org exists.
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_a_read_token_cannot_write_and_a_write_token_can(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    read = await create_token(ada, scope="read")
    write = await create_token(ada, scope="write")
    url = f"/api/v1/orgs/{workspace.org_id}/projects"

    refused = await bare_client.post(url, json={"name": "Second"}, headers=bearer(read["token"]))
    allowed = await bare_client.post(url, json={"name": "Second"}, headers=bearer(write["token"]))

    assert refused.status_code == 403
    assert refused.json()["code"] == "TOKEN_SCOPE"
    assert allowed.status_code == 201, allowed.text
    # The refusal changed nothing, and the read token still reads.
    listed = await bare_client.get(url, headers=bearer(read["token"]))
    assert [project["name"] for project in listed.json()] == ["Chatbot", "Second"]


async def test_a_read_token_cannot_remove_members_even_to_leave_an_org(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    # Leaving an organization is a DELETE that needs only `org:read`, so the permission class
    # alone would let a read-only token do it. The method check is what stops it.
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    owner_read = await create_token(owner, scope="read")
    member_read = await create_token(member, scope="read")
    member_write = await create_token(member, scope="write")
    members = f"/api/v1/orgs/{workspace.org_id}/members"

    for token, user in ((owner_read, member), (member_read, member)):
        response = await bare_client.delete(
            f"{members}/{user.user['id']}", headers=bearer(token["token"])
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "TOKEN_SCOPE"
    assert len((await owner.get(members)).json()) == 2

    # A write token acts as its user, and a member may leave.
    left = await bare_client.delete(
        f"{members}/{member.user['id']}", headers=bearer(member_write["token"])
    )
    assert left.status_code == 204
    assert len((await owner.get(members)).json()) == 1


async def test_a_token_is_limited_by_its_users_role_as_well_as_its_scope(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    viewer: Browser = await browser_factory("viewer@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, viewer, workspace.org_id, "viewer")
    token = await create_token(viewer, scope="write")

    response = await bare_client.post(
        f"/api/v1/orgs/{workspace.org_id}/projects",
        json={"name": "Nope"},
        headers=bearer(token["token"]),
    )

    # A write token does not widen a viewer; the role is what refuses, not the scope.
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_a_write_token_does_the_things_its_user_may_do(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    token = await create_token(owner, scope="write")
    headers = bearer(token["token"])

    key = await bare_client.post(
        f"/api/v1/projects/{workspace.project_id}/keys",
        json={"name": "from a token"},
        headers=headers,
    )
    assert key.status_code == 201
    patched = await bare_client.patch(
        f"/api/v1/projects/{workspace.project_id}", json={"retention_days": 7}, headers=headers
    )
    assert patched.status_code == 200
    invite = await bare_client.post(
        f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"}, headers=headers
    )
    assert invite.status_code == 201

    # It acted as its user: the key's creator is the token's owner.
    listed = (await owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    creators = {item["name"]: item["created_by"]["email"] for item in listed}
    assert creators["from a token"] == "owner@example.com"


async def test_pat_loses_access_when_membership_removed(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "admin")
    token = await create_token(member, scope="write")
    headers = bearer(token["token"])
    project_url = f"/api/v1/projects/{workspace.project_id}"
    org_url = f"/api/v1/orgs/{workspace.org_id}"
    for url in (f"{project_url}/traces", project_url, org_url):
        assert (await bare_client.get(url, headers=headers)).status_code == 200
    write = await bare_client.post(f"{org_url}/projects", json={"name": "P2"}, headers=headers)
    assert write.status_code == 201

    removed = await owner.delete(f"{org_url}/members/{member.user['id']}")
    assert removed.status_code == 204

    # The very next call: nothing is cached, and nothing says the org ever existed.
    for url in (f"{project_url}/traces", project_url, org_url, f"{org_url}/projects"):
        response = await bare_client.get(url, headers=headers)
        assert response.status_code == 404, url
        assert response.json()["code"] == "NOT_FOUND"
    again = await bare_client.post(f"{org_url}/projects", json={"name": "P3"}, headers=headers)
    assert again.status_code == 404
    # The token itself is intact: it still names its user.
    assert (await bare_client.get(ME, headers=headers)).status_code == 200


async def test_a_token_follows_its_users_role_when_it_changes(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "admin")
    token = await create_token(member, scope="write")
    url = f"/api/v1/orgs/{workspace.org_id}/projects"
    headers = bearer(token["token"])
    assert (await bare_client.post(url, json={"name": "P2"}, headers=headers)).status_code == 201

    demoted = await owner.patch(
        f"/api/v1/orgs/{workspace.org_id}/members/{member.user['id']}", json={"role": "viewer"}
    )
    assert demoted.status_code == 200

    refused = await bare_client.post(url, json={"name": "P3"}, headers=headers)
    assert refused.status_code == 403
    assert refused.json()["code"] == "FORBIDDEN"
    assert (await bare_client.get(url, headers=headers)).status_code == 200


async def test_me_answers_a_token_with_its_users_account(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(ada, scope="read")

    response = await bare_client.get(ME, headers=bearer(token["token"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert [m["org"]["id"] for m in body["memberships"]] == [workspace.org_id]
    assert body["has_password"] is True
    assert body["totp_enabled"] is False
    assert "set-cookie" not in response.headers


async def test_a_bearer_token_wins_over_the_cookies_of_another_user(
    browser_factory: Any,
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    grace: Browser = await browser_factory("grace@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(ada, scope="write")

    # Grace's browser, with Grace's session and CSRF cookies, sends Ada's token.
    me = await grace.http.get(ME, headers=bearer(token["token"]))
    created = await grace.http.post(
        f"/api/v1/orgs/{workspace.org_id}/projects",
        json={"name": "Second"},
        headers=bearer(token["token"]),
    )

    assert me.json()["user"]["email"] == "ada@example.com"
    assert created.status_code == 201, created.text  # Grace has no such org: this was Ada
    # And a token that does not exist is not rescued by the valid session cookie beside it.
    forged = await grace.http.get(ME, headers=bearer(f"spl_pat_{'a' * 12}_{'a' * 32}"))
    assert forged.status_code == 401


async def test_a_token_needs_no_origin_and_no_csrf_header(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(ada, scope="write")

    response = await bare_client.post(
        f"/api/v1/orgs/{workspace.org_id}/projects",
        json={"name": "Second"},
        headers=bearer(token["token"]),
    )

    assert response.status_code == 201
    assert len(bare_client.cookies) == 0


# --- refused or expired tokens -----------------------------------------------------------------


async def test_an_expired_token_is_401_token_expired(
    browser_factory: Any, bare_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(ada, expires_at="2099-01-01T00:00:00Z")
    assert (await bare_client.get(ME, headers=bearer(token["token"]))).status_code == 200
    await expire_token(session_factory, token["id"])

    for path in (ME, f"/api/v1/projects/{workspace.project_id}/traces"):
        response = await bare_client.get(path, headers=bearer(token["token"]))
        assert response.status_code == 401, path
        assert response.json()["code"] == "TOKEN_EXPIRED"


async def test_a_wrong_secret_never_reveals_that_a_token_expired_or_was_revoked(
    browser_factory: Any, bare_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    expired = await create_token(ada, expires_at="2099-01-01T00:00:00Z")
    revoked = await create_token(ada)
    await expire_token(session_factory, expired["id"])
    await ada.delete(f"{TOKENS}/{revoked['id']}")

    for token in (expired, revoked):
        forged = f"{token['prefix']}_{'a' * 32}"
        response = await bare_client.get(ME, headers=bearer(forged))
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"


async def test_a_token_that_is_both_revoked_and_expired_is_plain_401(
    browser_factory: Any, bare_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    token = await create_token(ada, expires_at="2099-01-01T00:00:00Z")
    await expire_token(session_factory, token["id"])
    await ada.delete(f"{TOKENS}/{token['id']}")

    response = await bare_client.get(ME, headers=bearer(token["token"]))

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer spl_pat_",
        "Bearer spl_pat_short",
        f"Bearer spl_pat_{'a' * 12}",
        f"Bearer spl_pat_{'a' * 12}_{'a' * 31}",
        f"Bearer spl_pat_{'a' * 12}_{'a' * 32}",  # well formed, unknown
        f"Bearer SPL_PAT_{'a' * 12}_{'a' * 32}",
        "Bearer sk-not-ours",
        "Bearer",
    ],
)
async def test_a_bearer_that_is_not_a_known_token_is_401(
    bare_client: httpx.AsyncClient, authorization: str
) -> None:
    response = await bare_client.get(ME, headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert "set-cookie" not in response.headers


async def test_a_token_is_not_an_api_key_and_a_key_is_not_a_token(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)
    token = await create_token(ada, scope="write")

    # Ingestion takes project API keys only.
    ingest = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id())]},
        headers=bearer(token["token"]),
    )
    assert ingest.status_code == 401
    assert ingest.json()["code"] == "UNAUTHORIZED"
    # And the key is refused where only a user's credential will do.
    refused = await client.get(TOKENS, headers=bearer(workspace.api_key))
    assert refused.status_code == 403
    assert refused.json()["code"] == "KEY_SCOPE"


# --- last used ---------------------------------------------------------------------------------


async def test_last_used_is_written_at_most_once_a_minute(
    browser_factory: Any, bare_client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    token = await create_token(ada)
    headers = bearer(token["token"])
    assert await row(session_factory, token["id"], "last_used_at") is None

    await bare_client.get(ME, headers=headers)
    first = await row(session_factory, token["id"], "last_used_at")
    await bare_client.get(ME, headers=headers)
    second = await row(session_factory, token["id"], "last_used_at")

    assert first is not None
    assert second == first  # within the minute: no second write

    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE personal_access_tokens SET last_used_at = now() - interval '2 minutes' "
                "WHERE id = :id"
            ),
            {"id": token["id"]},
        )
        await db.commit()
    await bare_client.get(ME, headers=headers)
    third = await row(session_factory, token["id"], "last_used_at")

    assert third is not None
    assert third > first  # the two-minute-old value was replaced
    assert datetime.now(UTC) - third < timedelta(minutes=1)
    listed = (await ada.get(TOKENS)).json()
    assert listed[0]["last_used_at"] is not None


# --- sessions only -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", TOKENS),
        ("POST", TOKENS),
        ("DELETE", f"{TOKENS}/00000000-0000-4000-8000-000000000000"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/auth/sessions"),
        ("DELETE", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/orgs"),
        ("GET", "/api/v1/invites/preview"),
        ("POST", "/api/v1/invites/accept"),
        ("GET", "/api/v1/auth/totp"),
        ("GET", "/api/v1/auth/oauth/identities"),
    ],
)
async def test_a_token_is_refused_on_a_session_only_route(
    browser_factory: Any, bare_client: httpx.AsyncClient, method: str, path: str
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    token = await create_token(ada, scope="write")

    response = await bare_client.request(
        method,
        path,
        json={"name": "x", "scope": "read", "token": "x"} if method == "POST" else None,
        params={"token": "x"} if path.endswith("preview") else None,
        headers=bearer(token["token"]),
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "SESSION_REQUIRED"
    assert "set-cookie" not in response.headers


async def test_a_token_cannot_mint_tokens(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    token = await create_token(ada, scope="write")

    response = await bare_client.post(
        TOKENS, json={"name": "child", "scope": "write"}, headers=bearer(token["token"])
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SESSION_REQUIRED"
    assert len((await ada.get(TOKENS)).json()) == 1


async def test_a_token_cannot_accept_an_invite(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    invitee: Browser = await browser_factory("invitee@example.com")
    workspace = await create_workspace(owner)
    invite = await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "member"})
    invite_token = invite.json()["url"].rsplit("/", 1)[1]
    token = await create_token(invitee, scope="write")

    refused = await bare_client.post(
        "/api/v1/invites/accept", json={"token": invite_token}, headers=bearer(token["token"])
    )

    assert refused.status_code == 403
    assert refused.json()["code"] == "SESSION_REQUIRED"
    # The invite was not spent, so the person can still take it in their browser.
    assert (
        await invitee.post("/api/v1/invites/accept", json={"token": invite_token})
    ).status_code == 200


async def test_a_token_is_refused_on_routes_that_need_no_sign_in(
    browser_factory: Any, bare_client: httpx.AsyncClient
) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    token = await create_token(ada, scope="write")
    headers = bearer(token["token"])

    login = await bare_client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "correct horse battery"},
        headers=headers,
    )
    signup = await bare_client.post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "password": "correct horse battery", "name": "New"},
        headers=headers,
    )

    for response in (login, signup):
        assert response.status_code == 403
        assert response.json()["code"] == "SESSION_REQUIRED"
        assert "set-cookie" not in response.headers
    assert len(bare_client.cookies) == 0
    # Nothing was created: the address is still free.
    free = await bare_client.post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "password": "correct horse battery", "name": "New"},
        headers={"Origin": "http://testserver"},
    )
    assert free.status_code == 201


# --- two-factor authentication -----------------------------------------------------------------


async def test_a_token_of_a_member_without_two_factor_is_refused_in_an_org_that_requires_it(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    member = await totp_browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    token = await create_token(member, scope="write")
    headers = bearer(token["token"])
    project = f"/api/v1/projects/{workspace.project_id}"
    assert (await totp_client.get(project, headers=headers)).status_code == 200
    await enroll(owner, clock)
    assert (
        await owner.patch(f"/api/v1/orgs/{workspace.org_id}", json={"require_2fa": True})
    ).status_code == 200

    for response in (
        await totp_client.get(project, headers=headers),
        await totp_client.get(f"{project}/traces", headers=headers),
        await totp_client.post(f"{project}/keys", json={"name": "k"}, headers=headers),
        await totp_client.get(f"/api/v1/orgs/{workspace.org_id}", headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "TWO_FACTOR_REQUIRED"
    # The way out stays open: the token can still name its user.
    assert (await totp_client.get(ME, headers=headers)).status_code == 200

    await enroll(member, clock)

    assert (await totp_client.get(project, headers=headers)).status_code == 200


async def test_the_two_factor_check_comes_after_membership_for_a_token(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    stranger = await totp_browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)
    await owner.patch(f"/api/v1/orgs/{workspace.org_id}", json={"require_2fa": True})
    token = await create_token(stranger, scope="read")

    response = await totp_client.get(
        f"/api/v1/projects/{workspace.project_id}", headers=bearer(token["token"])
    )

    assert response.status_code == 404


# --- permission classes ------------------------------------------------------------------------


def test_every_permission_has_a_class() -> None:
    assert set(PERMISSION_CLASSES) == set(Permission)
    assert {permission_class(permission) for permission in Permission} <= {"read", "write"}


def test_the_read_permissions_are_exactly_the_documented_three() -> None:
    reads = {p for p in Permission if permission_class(p) == "read"}

    assert reads == {Permission.ORG_READ, Permission.PROJECT_READ, Permission.AUDIT_READ}


def test_everything_that_changes_something_is_a_write() -> None:
    writes = {p for p in Permission if permission_class(p) == "write"}

    assert writes == set(Permission) - {
        Permission.ORG_READ,
        Permission.PROJECT_READ,
        Permission.AUDIT_READ,
    }
    assert Permission.ORG_SECURITY in writes
