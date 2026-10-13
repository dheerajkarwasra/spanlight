"""Organizations, members (last-owner protection), invites and the audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Invite
from app.services.slugs import RESERVED_ORG_SLUGS
from tests.helpers import Browser, create_workspace, join_with_role


async def test_create_org_makes_caller_owner_and_uniquifies_slug(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    first = (await ada.post("/api/v1/orgs", json={"name": "Acme Inc."})).json()
    second = (await ada.post("/api/v1/orgs", json={"name": "Acme Inc."})).json()

    assert first["slug"] == "acme-inc"
    assert second["slug"].startswith("acme-inc-") and second["slug"] != first["slug"]
    me = (await ada.get("/api/v1/auth/me")).json()
    assert [m["role"] for m in me["memberships"]] == ["owner", "owner"]
    org = (await ada.get(f"/api/v1/orgs/{first['id']}")).json()
    assert org["role"] == "owner"


async def test_org_slug_never_collides_with_a_dashboard_path(browser_factory: Any) -> None:
    ada: Browser = await browser_factory("ada@example.com")
    login = (await ada.post("/api/v1/orgs", json={"name": "Login"})).json()
    api = (await ada.post("/api/v1/orgs", json={"name": "API"})).json()
    plain = (await ada.post("/api/v1/orgs", json={"name": "Globex"})).json()

    assert login["slug"] not in RESERVED_ORG_SLUGS and login["slug"].startswith("login-")
    assert api["slug"] not in RESERVED_ORG_SLUGS and api["slug"].startswith("api-")
    assert plain["slug"] == "globex"


async def test_last_owner_cannot_be_demoted_or_removed(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    owner_id = owner.user["id"]

    demote = await owner.patch(
        f"/api/v1/orgs/{workspace.org_id}/members/{owner_id}", json={"role": "admin"}
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "LAST_OWNER"
    leave = await owner.delete(f"/api/v1/orgs/{workspace.org_id}/members/{owner_id}")
    assert leave.status_code == 409


async def test_owner_can_step_down_once_another_owner_exists(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    second: Browser = await browser_factory("second@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, second, workspace.org_id, "owner")

    demote = await owner.patch(
        f"/api/v1/orgs/{workspace.org_id}/members/{owner.user['id']}", json={"role": "admin"}
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "admin"
    # Now `second` is the last owner.
    blocked = await second.patch(
        f"/api/v1/orgs/{workspace.org_id}/members/{second.user['id']}", json={"role": "member"}
    )
    assert blocked.json()["code"] == "LAST_OWNER"


async def test_admin_cannot_grant_or_alter_owner(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    admin: Browser = await browser_factory("admin@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")
    await join_with_role(owner, member, workspace.org_id, "member")
    org = workspace.org_id

    promote = await admin.patch(
        f"/api/v1/orgs/{org}/members/{member.user['id']}", json={"role": "owner"}
    )
    assert promote.status_code == 403
    demote_owner = await admin.patch(
        f"/api/v1/orgs/{org}/members/{owner.user['id']}", json={"role": "viewer"}
    )
    assert demote_owner.status_code == 403
    assert (
        await admin.post(f"/api/v1/orgs/{org}/invites", json={"role": "owner"})
    ).status_code == 403
    ok = await admin.patch(
        f"/api/v1/orgs/{org}/members/{member.user['id']}", json={"role": "viewer"}
    )
    assert ok.status_code == 200


async def test_member_can_leave_but_not_remove_others(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")

    kick = await member.delete(f"/api/v1/orgs/{workspace.org_id}/members/{owner.user['id']}")
    assert kick.status_code == 403
    leave = await member.delete(f"/api/v1/orgs/{workspace.org_id}/members/{member.user['id']}")
    assert leave.status_code == 204
    assert (await member.get(f"/api/v1/orgs/{workspace.org_id}")).status_code == 404


async def test_invite_flow(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    guest: Browser = await browser_factory("guest@example.com")
    workspace = await create_workspace(owner)

    created = await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "member"})
    assert created.status_code == 201
    invite = created.json()
    assert invite["url"].startswith("http://testserver/invite/")
    token = invite["url"].rsplit("/", 1)[1]

    pending = (await owner.get(f"/api/v1/orgs/{workspace.org_id}/invites")).json()
    assert [item["id"] for item in pending] == [invite["id"]]
    assert "token" not in pending[0] and "url" not in pending[0]

    accepted = await guest.post("/api/v1/invites/accept", json={"token": token})
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "member"
    assert accepted.json()["org"]["id"] == workspace.org_id

    reused = await guest.post("/api/v1/invites/accept", json={"token": token})
    assert reused.status_code == 404
    assert (await owner.get(f"/api/v1/orgs/{workspace.org_id}/invites")).json() == []


async def test_invite_preview_shows_org_and_role_without_accepting(
    browser_factory: Any,
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    guest: Browser = await browser_factory("guest@example.com")
    workspace = await create_workspace(owner, org_name="Acme AI")
    created = (
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "admin"})
    ).json()
    token = created["url"].rsplit("/", 1)[1]

    preview = await guest.get("/api/v1/invites/preview", params={"token": token})
    assert preview.status_code == 200
    body = preview.json()
    assert body["org"]["id"] == workspace.org_id
    assert body["org"]["name"] == "Acme AI"
    assert set(body["org"]) == {"id", "name", "slug"}
    assert body["role"] == "admin"
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromisoformat(
        created["expires_at"]
    )

    # Previewing neither consumes the invite nor grants access.
    assert (await guest.get(f"/api/v1/orgs/{workspace.org_id}")).status_code == 404
    accepted = await guest.post("/api/v1/invites/accept", json={"token": token})
    assert accepted.status_code == 200


async def test_invite_preview_requires_login(client: Any) -> None:
    response = await client.get("/api/v1/invites/preview", params={"token": "whatever"})
    assert response.status_code == 401


async def test_invite_preview_hides_unknown_used_and_expired_tokens(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    guest: Browser = await browser_factory("guest@example.com")
    workspace = await create_workspace(owner)

    used = (
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
    ).json()
    used_token = used["url"].rsplit("/", 1)[1]
    assert (
        await guest.post("/api/v1/invites/accept", json={"token": used_token})
    ).status_code == 200

    expired = (
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
    ).json()
    async with session_factory() as db:
        await db.execute(
            update(Invite).where(Invite.id == expired["id"]).values(expires_at=Invite.created_at)
        )
        await db.commit()
    expired_token = expired["url"].rsplit("/", 1)[1]

    details: set[str] = set()
    for token in ("not-a-real-token", used_token, expired_token):
        response = await guest.get("/api/v1/invites/preview", params={"token": token})
        assert response.status_code == 404
        details.add(response.json()["detail"])
    assert len(details) == 1


async def test_invite_accept_requires_login(client: Any) -> None:
    response = await client.post("/api/v1/invites/accept", json={"token": "whatever"})
    assert response.status_code == 401


async def test_expired_and_revoked_invites_cannot_be_used(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    guest: Browser = await browser_factory("guest@example.com")
    workspace = await create_workspace(owner)

    expired = (
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
    ).json()
    async with session_factory() as db:
        await db.execute(
            update(Invite).where(Invite.id == expired["id"]).values(expires_at=Invite.created_at)
        )
        await db.commit()
    expired_token = expired["url"].rsplit("/", 1)[1]
    assert (
        await guest.post("/api/v1/invites/accept", json={"token": expired_token})
    ).status_code == 404

    revoked = (
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
    ).json()
    revoke = await owner.delete(f"/api/v1/orgs/{workspace.org_id}/invites/{revoked['id']}")
    assert revoke.status_code == 204
    revoked_token = revoked["url"].rsplit("/", 1)[1]
    assert (
        await guest.post("/api/v1/invites/accept", json={"token": revoked_token})
    ).status_code == 404


async def test_audit_log_records_actions_and_paginates(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)  # org.create, project.create, key.create
    for index in range(3):
        await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
        await owner.patch(f"/api/v1/projects/{workspace.project_id}", json={"name": f"Bot {index}"})

    seen: list[dict[str, Any]] = []
    cursor = None
    while True:
        params = {"limit": 4, **({"cursor": cursor} if cursor else {})}
        page = (await owner.get(f"/api/v1/orgs/{workspace.org_id}/audit", params=params)).json()
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    actions = [event["action"] for event in seen]
    assert len(actions) == 9 == len({event["id"] for event in seen})
    assert actions[-3:] == ["key.create", "project.create", "org.create"]
    assert seen[0]["actor"]["email"] == "owner@example.com"
    created = [event["created_at"] for event in seen]
    assert created == sorted(created, reverse=True)


async def test_invalid_cursor_is_422(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    response = await owner.get(f"/api/v1/orgs/{workspace.org_id}/audit", params={"cursor": "%%%"})
    assert response.status_code == 422
