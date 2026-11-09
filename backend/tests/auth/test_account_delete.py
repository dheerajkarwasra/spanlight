"""Deleting your own account: `DELETE /api/v1/auth/me`.

It needs a browser session, the typed word `delete` and the owner's re-proof. It is refused while
the user is the only owner of an organization, and otherwise removes the user row in one
transaction: what belongs to the user cascades away, what they made in a project stays with its
author cleared, and every org they were in keeps an audit trail that still verifies.
"""

import hashlib
import uuid

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.totp import hash_recovery_code
from app.db.models import (
    AuditAction,
    AuditEvent,
    Membership,
    MembershipRole,
    OAuthIdentity,
    RecoveryCode,
    User,
)
from app.services.audit import check_audit_chain
from tests.auth.conftest import FakeClock
from tests.auth.test_totp_routes import enroll
from tests.conftest import BrowserFactory
from tests.helpers import (
    Browser,
    bearer,
    create_api_key,
    create_token,
    create_workspace,
    join_with_role,
)

SessionFactory = async_sessionmaker[AsyncSession]

ME = "/api/v1/auth/me"
PASSWORD = "correct horse battery"  # what the browser factory signs users up with
# Every table whose rows belong to the user and must go with them.
OWNED_TABLES = (
    "sessions",
    "personal_access_tokens",
    "oauth_identities",
    "recovery_codes",
    "memberships",
    "email_tokens",
)


async def _delete(browser: Browser, **body: object) -> httpx.Response:
    return await browser.delete(ME, json={"password": PASSWORD, "confirm": "delete", **body})


async def _create_org(browser: Browser, name: str) -> dict[str, str]:
    response = await browser.post("/api/v1/orgs", json={"name": name})
    assert response.status_code == 201, response.text
    org: dict[str, str] = response.json()
    return org


async def _user_exists(factory: SessionFactory, user_id: str) -> bool:
    async with factory() as db:
        return await db.get(User, uuid.UUID(user_id)) is not None


async def _count(factory: SessionFactory, table: str, user_id: str) -> int:
    async with factory() as db:
        count: int = (
            await db.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :id"), {"id": user_id}
            )
        ).scalar_one()
        return count


async def _audit(factory: SessionFactory, action: AuditAction) -> list[AuditEvent]:
    async with factory() as db:
        return list(
            (await db.scalars(select(AuditEvent).where(AuditEvent.action == action.value))).all()
        )


async def test_delete_wrong_password(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    response = await _delete(ada, password="not the password")

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"
    assert await _user_exists(session_factory, ada.user["id"])


async def test_delete_requires_typed_confirm(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    for typed in ("DELETE", " delete", "", "yes"):
        response = await _delete(ada, confirm=typed)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "CONFIRMATION_MISMATCH"
    missing = await ada.delete(ME, json={"password": PASSWORD})
    assert missing.status_code == 422, missing.text

    assert await _user_exists(session_factory, ada.user["id"])


async def test_delete_refused_for_sole_owner(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    grace = await browser_factory("grace@example.com")
    alone = await _create_org(ada, "Alone")
    shared = await _create_org(ada, "Shared")
    await join_with_role(ada, grace, shared["id"], "owner")
    graces = await _create_org(grace, "Grace's")
    await join_with_role(grace, ada, graces["id"], "member")

    response = await _delete(ada)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "SOLE_OWNER"
    assert body["orgs"] == [{"id": alone["id"], "slug": alone["slug"], "name": "Alone"}]
    # Nothing was deleted, and the session still works.
    assert await _user_exists(session_factory, ada.user["id"])
    assert await _count(session_factory, "memberships", ada.user["id"]) == 3
    assert (await ada.get(ME)).status_code == 200
    assert await _audit(session_factory, AuditAction.USER_DELETE) == []


async def test_co_owner_can_delete(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    grace = await browser_factory("grace@example.com")
    ada = await browser_factory("ada@example.com")
    org = await _create_org(grace, "Acme")
    await join_with_role(grace, ada, org["id"], "owner")

    response = await _delete(ada)

    assert response.status_code == 204, response.text
    assert not await _user_exists(session_factory, ada.user["id"])
    async with session_factory() as db:
        members = (
            await db.execute(
                select(Membership.user_id, Membership.role).where(
                    Membership.org_id == uuid.UUID(org["id"])
                )
            )
        ).all()
    assert [(str(user_id), role) for user_id, role in members] == [
        (grace.user["id"], MembershipRole.OWNER)
    ]
    assert (await grace.get(f"/api/v1/orgs/{org['id']}")).status_code == 200


async def test_member_delete_removes_user_rows(
    email_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    grace = await email_browser_factory("grace@example.com")
    # Signing up with email configured leaves a verification token for the user.
    ada = await email_browser_factory("ada@example.com")
    workspace = await create_workspace(grace)
    await join_with_role(grace, ada, workspace.org_id, "member")
    await create_token(ada)
    project = f"/api/v1/projects/{workspace.project_id}"
    dataset = await ada.post(f"{project}/datasets", json={"name": "golden"})
    assert dataset.status_code == 201, dataset.text
    prompt = await ada.post(
        f"{project}/prompts",
        json={"name": "greet", "kind": "text", "template": {"kind": "text", "text": "Hi"}},
    )
    assert prompt.status_code == 201, prompt.text
    async with session_factory() as db:
        ada_id = uuid.UUID(ada.user["id"])
        db.add(OAuthIdentity(user_id=ada_id, provider="github", subject="42"))
        # One row per stored format: NULL (before code_length existed), 10 and 28.
        db.add_all(
            RecoveryCode(
                user_id=ada_id, code_hash=hashlib.sha256(str(n).encode()).digest(), code_length=n
            )
            for n in (None, 10, 28)
        )
        await db.commit()
    for table in OWNED_TABLES:
        assert await _count(session_factory, table, ada.user["id"]) >= 1, table

    response = await _delete(ada)

    assert response.status_code == 204, response.text
    assert not await _user_exists(session_factory, ada.user["id"])
    for table in OWNED_TABLES:
        assert await _count(session_factory, table, ada.user["id"]) == 0, table
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        dataset_author = (
            await db.execute(
                text("SELECT created_by FROM datasets WHERE id = :id"),
                {"id": dataset.json()["id"]},
            )
        ).one()
        # The immutable prompt versions allow exactly this one update.
        version_authors = (
            await db.execute(
                text(
                    "SELECT v.created_by FROM prompt_versions v "
                    "JOIN prompts p ON p.id = v.prompt_id WHERE p.name = 'greet'"
                )
            )
        ).all()
        # No foreign key to `users` may block the delete or leave a dangling id behind.
        blocking = (
            await db.execute(
                text(
                    "SELECT conrelid::regclass::text FROM pg_constraint "
                    "WHERE contype = 'f' AND confrelid = 'users'::regclass "
                    "AND confdeltype NOT IN ('c', 'n')"
                )
            )
        ).all()
    assert dataset_author.created_by is None
    assert [row.created_by for row in version_authors] == [None]
    assert blocking == []
    assert (await grace.get(f"/api/v1/orgs/{workspace.org_id}")).status_code == 200


async def test_delete_keeps_audit_rows_and_chain(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    grace = await browser_factory("grace@example.com")
    ada = await browser_factory("ada@example.com")
    first = await _create_org(grace, "Acme")
    second = await _create_org(grace, "Beta")
    for org in (first, second):
        await join_with_role(grace, ada, org["id"], "member")
    assert (await ada.patch(ME, json={"name": "Ada Lovelace"})).status_code == 200

    response = await _delete(ada)

    assert response.status_code == 204, response.text
    earlier = await _audit(session_factory, AuditAction.USER_UPDATE)
    deleted = await _audit(session_factory, AuditAction.USER_DELETE)
    for events in (earlier, deleted):
        assert sorted(str(event.org_id) for event in events) == sorted([first["id"], second["id"]])
        for event in events:
            assert str(event.actor_user_id) == ada.user["id"]
            assert event.actor_email == "ada@example.com"
    for event in deleted:
        assert event.target_type == "user"
        assert event.target_id == ada.user["id"]
    async with session_factory() as db:
        for org in (first, second):
            stored = await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.org_id == uuid.UUID(org["id"]))
            )
            check = await check_audit_chain(db, uuid.UUID(org["id"]))
            assert check.first_broken_seq is None
            assert check.checked == stored


async def test_delete_clears_cookies_and_session(
    browser_factory: BrowserFactory, bare_client: httpx.AsyncClient
) -> None:
    ada = await browser_factory("ada@example.com")
    stolen = ada.http.cookies["spl_session"]

    response = await _delete(ada)

    assert response.status_code == 204, response.text
    cleared = response.headers.get_list("set-cookie")
    for name in ("spl_session", "spl_csrf"):
        assert any(cookie.startswith(f"{name}=") and "Max-Age=0" in cookie for cookie in cleared)
    assert (await ada.get(ME)).status_code == 401
    # The session row is gone too, so the old cookie is worthless.
    bare_client.cookies.set("spl_session", stolen)
    assert (await bare_client.get(ME)).status_code == 401


async def test_stolen_session_cannot_delete(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    response = await ada.delete(ME, json={"confirm": "delete"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"
    assert await _user_exists(session_factory, ada.user["id"])


async def test_delete_with_2fa_needs_the_code(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    # A stolen session that also knows the password is still missing the second factor.
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)

    response = await _delete(ada)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "TOTP_CODE_REQUIRED"
    assert await _user_exists(session_factory, ada.user["id"])


async def test_delete_wrong_code_counts_toward_the_limit(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)

    for _ in range(5):
        wrong = await _delete(ada, code="123456")
        assert wrong.status_code == 422, wrong.text
        assert wrong.json()["code"] == "INVALID_TOTP_CODE"
    blocked = await _delete(ada, code="123456")

    assert blocked.status_code == 429, blocked.text
    assert await _user_exists(session_factory, ada.user["id"])


async def test_delete_with_a_valid_totp_code(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    secret, _ = await enroll(ada, clock)

    response = await _delete(ada, code=clock.next_code(secret))

    assert response.status_code == 204, response.text
    assert not await _user_exists(session_factory, ada.user["id"])


@pytest.mark.parametrize("code", ["abcde-fghij", "abcdefg-hijklmn-opqrstu-vwxyz23"])
async def test_delete_with_a_recovery_code(
    totp_browser_factory: BrowserFactory,
    session_factory: SessionFactory,
    clock: FakeClock,
    code: str,
) -> None:
    ada = await totp_browser_factory("ada@example.com")
    await enroll(ada, clock)
    async with session_factory() as db:
        db.add(RecoveryCode(user_id=uuid.UUID(ada.user["id"]), code_hash=hash_recovery_code(code)))
        await db.commit()

    response = await _delete(ada, code=f"  {code.upper()}  ")

    assert response.status_code == 204, response.text
    assert not await _user_exists(session_factory, ada.user["id"])
    assert await _count(session_factory, "recovery_codes", ada.user["id"]) == 0
    again = await _delete(ada, code=code)
    assert again.status_code == 401, again.text
    assert again.json()["code"] == "UNAUTHORIZED"


async def test_delete_refuses_demo_user(email_client: httpx.AsyncClient) -> None:
    assert (await email_client.post("/api/v1/demo/session")).status_code == 200
    csrf = {"X-CSRF-Token": email_client.cookies["spl_csrf"]}

    response = await email_client.request(
        "DELETE", ME, json={"password": PASSWORD, "confirm": "delete"}, headers=csrf
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
    assert (await email_client.get(ME)).status_code == 200


async def test_delete_refuses_bearer_token(
    browser_factory: BrowserFactory,
    bare_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    ada = await browser_factory("ada@example.com")
    token = await create_token(ada, scope="write")

    response = await bare_client.request(
        "DELETE",
        ME,
        json={"password": PASSWORD, "confirm": "delete"},
        headers=bearer(token["token"]),
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "SESSION_REQUIRED"
    assert await _user_exists(session_factory, ada.user["id"])


async def test_account_deletion_strips_read_keys_and_audits_each_change(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    member = await browser_factory("member@example.com")
    first = await create_workspace(owner)
    second = await create_workspace(owner, org_name="Second")
    await join_with_role(owner, member, first.org_id, "member")
    await join_with_role(owner, member, second.org_id, "member")
    reader = await create_api_key(member, first.project_id, scopes=["prompts:read", "traces:read"])
    mixed = await create_api_key(member, second.project_id, scopes=["traces:read", "ingest:write"])
    ingest = await create_api_key(member, first.project_id)
    first_traces = f"/api/v1/projects/{first.project_id}/traces"
    second_traces = f"/api/v1/projects/{second.project_id}/traces"
    assert (await client.get(first_traces, headers=bearer(reader["secret"]))).status_code == 200
    assert (await client.get(second_traces, headers=bearer(mixed["secret"]))).status_code == 200

    deleted = await _delete(member)

    assert deleted.status_code == 204, deleted.text
    assert not await _user_exists(session_factory, member.user["id"])
    assert (await client.get(first_traces, headers=bearer(reader["secret"]))).status_code == 401
    assert (await client.get(second_traces, headers=bearer(mixed["secret"]))).status_code == 403
    assert (
        await client.post("/v1/traces", json={"spans": []}, headers=bearer(mixed["secret"]))
    ).status_code == 200
    assert (
        await client.post("/v1/traces", json={"spans": []}, headers=bearer(ingest["secret"]))
    ).status_code == 200
    revoked = await _audit(session_factory, AuditAction.KEY_REVOKE)
    narrowed = await _audit(session_factory, AuditAction.KEY_SCOPES_CHANGE)
    assert len(revoked) == len(narrowed) == 1
    assert revoked[0].target_id == reader["id"]
    assert revoked[0].metadata_ == {
        "name": reader["name"],
        "prefix": reader["prefix"],
        "project_id": first.project_id,
        "reason": "account_delete",
        "member_user_id": member.user["id"],
    }
    assert narrowed[0].target_id == mixed["id"]
    assert narrowed[0].metadata_ == {
        "name": mixed["name"],
        "prefix": mixed["prefix"],
        "project_id": second.project_id,
        "from": ["traces:read", "ingest:write"],
        "to": ["ingest:write"],
        "reason": "account_delete",
        "member_user_id": member.user["id"],
    }
    assert str(revoked[0].actor_user_id) == str(narrowed[0].actor_user_id) == member.user["id"]
    assert str(revoked[0].org_id) == first.org_id
    assert str(narrowed[0].org_id) == second.org_id
    async with session_factory() as db:
        first_chain = await check_audit_chain(db, uuid.UUID(first.org_id))
        second_chain = await check_audit_chain(db, uuid.UUID(second.org_id))
    assert first_chain.first_broken_seq is None
    assert second_chain.first_broken_seq is None
