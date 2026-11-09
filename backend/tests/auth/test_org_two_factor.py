"""Organizations that require two-factor authentication from their members."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent
from tests.auth.conftest import FakeClock
from tests.auth.test_totp_routes import enroll
from tests.conftest import BrowserFactory
from tests.helpers import Browser, bearer, create_workspace, join_with_role, new_trace_id, span
from tests.orgs.test_org_delete_races import _waiting

SessionFactory = async_sessionmaker[AsyncSession]

ME = "/api/v1/auth/me"


def org_url(org_id: str) -> str:
    return f"/api/v1/orgs/{org_id}"


async def require_two_factor(owner: Browser, org_id: str, value: bool = True) -> httpx.Response:
    return await owner.patch(org_url(org_id), json={"require_2fa": value})


# --- turning it on and off ---------------------------------------------------------------------


@pytest.mark.serial
async def test_requiring_two_factor_takes_the_user_lock_before_the_org(
    totp_browser_factory: BrowserFactory, clock: FakeClock, session_factory: SessionFactory
) -> None:
    """The proof routes lock the user, then the organization. Taking them the other way round
    deadlocked the same owner deleting a project in another tab, and one of the two got a 500."""
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)
    org = {"id": workspace.org_id}

    async with session_factory() as other_tab:
        await other_tab.execute(
            text("SELECT 1 FROM users WHERE id = :id FOR NO KEY UPDATE"),
            {"id": owner.user["id"]},
        )
        request = require_two_factor(owner, workspace.org_id)
        async with _waiting(session_factory, request) as requiring:
            async with session_factory() as probe:
                free = await probe.scalar(
                    text("SELECT id FROM organizations WHERE id = :id FOR UPDATE SKIP LOCKED"), org
                )
                assert free is not None, "the request holds the organization while it waits"
            await other_tab.commit()
            response = await requiring
    assert response.status_code == 200, response.text


async def test_an_owner_with_two_factor_can_require_it(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)

    response = await require_two_factor(owner, workspace.org_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["require_2fa"] is True
    assert body["id"] == workspace.org_id
    assert set(body) == {"id", "name", "slug", "is_demo", "require_2fa"}
    assert (await owner.get(org_url(workspace.org_id))).json()["require_2fa"] is True


async def test_an_owner_without_two_factor_cannot_require_it(
    totp_browser_factory: BrowserFactory,
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await require_two_factor(owner, workspace.org_id)

    assert response.status_code == 409
    assert response.json()["code"] == "TWO_FACTOR_NOT_ENABLED"
    assert (await owner.get(org_url(workspace.org_id))).json()["require_2fa"] is False


async def test_requiring_it_needs_credentials_keys(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await require_two_factor(owner, workspace.org_id)

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_CONFIGURED"
    assert "CREDENTIALS_KEYS" in response.json()["detail"]


async def test_an_admin_cannot_change_it(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    admin = await totp_browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")
    await enroll(admin, clock)

    response = await require_two_factor(admin, workspace.org_id)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert (await owner.get(org_url(workspace.org_id))).json()["require_2fa"] is False


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_lower_roles_cannot_change_it(
    totp_browser_factory: BrowserFactory, role: str
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    other = await totp_browser_factory("other@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, other, workspace.org_id, role)

    assert (await require_two_factor(other, workspace.org_id)).status_code == 403


async def test_a_stranger_gets_404(totp_browser_factory: BrowserFactory) -> None:
    owner = await totp_browser_factory("owner@example.com")
    stranger = await totp_browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)

    assert (await require_two_factor(stranger, workspace.org_id)).status_code == 404


async def test_require_2fa_must_be_a_boolean(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)

    for body in ({}, {"require_2fa": None}, {"require_2fa": "maybe"}, {"retention": 3}):
        response = await owner.patch(org_url(workspace.org_id), json=body)
        assert response.status_code == 422, body
        assert response.json()["code"] == "VALIDATION_ERROR"


async def test_changing_it_is_audited_with_the_old_and_new_value(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)

    await require_two_factor(owner, workspace.org_id, True)
    await require_two_factor(owner, workspace.org_id, True)  # no change, no event
    await require_two_factor(owner, workspace.org_id, False)

    async with session_factory() as db:
        events = (
            await db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "org.update")
                .order_by(AuditEvent.created_at)
            )
        ).all()
    assert [event.metadata_ for event in events] == [
        {"require_2fa": {"from": False, "to": True}},
        {"require_2fa": {"from": True, "to": False}},
    ]
    for event in events:
        assert str(event.org_id) == workspace.org_id
        assert str(event.actor_user_id) == owner.user["id"]
        assert event.target_type == "org"
        assert event.target_id == workspace.org_id


async def test_a_rename_and_the_requirement_change_together_in_one_audit_event(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    await enroll(owner, clock)

    response = await owner.patch(
        org_url(workspace.org_id), json={"name": "Acme Labs", "require_2fa": True}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Acme Labs"
    assert response.json()["require_2fa"] is True
    async with session_factory() as db:
        (event,) = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "org.update"))
        ).all()
    assert event.metadata_ == {
        "name": {"from": "Acme", "to": "Acme Labs"},
        "require_2fa": {"from": False, "to": True},
    }


async def test_a_refused_requirement_leaves_a_rename_in_the_same_request_unapplied(
    totp_browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    # The owner has no two-factor authentication, so turning the requirement on is refused; the
    # rename that came with it must not slip through on its own.
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")

    response = await owner.patch(
        org_url(workspace.org_id), json={"name": "Acme Labs", "require_2fa": True}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TWO_FACTOR_NOT_ENABLED"
    assert (await owner.get(org_url(workspace.org_id))).json()["name"] == "Acme"
    async with session_factory() as db:
        assert (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "org.update"))
        ).all() == []


async def test_it_can_be_turned_off_again(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    member = await totp_browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)
    assert (await member.get(org_url(workspace.org_id))).status_code == 403

    off = await require_two_factor(owner, workspace.org_id, False)

    assert off.status_code == 200
    assert off.json()["require_2fa"] is False
    assert (await member.get(org_url(workspace.org_id))).status_code == 200


# --- what it does to members -------------------------------------------------------------------


async def test_members_without_two_factor_are_locked_out_of_the_org_until_they_enable_it(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    member = await totp_browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)

    org = await member.get(org_url(workspace.org_id))
    members = await member.get(f"{org_url(workspace.org_id)}/members")
    project = await member.get(f"/api/v1/projects/{workspace.project_id}")
    traces = await member.get(f"/api/v1/projects/{workspace.project_id}/traces")
    write = await member.post(f"/api/v1/projects/{workspace.project_id}/keys", json={"name": "k"})

    for response in (org, members, project, traces, write):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "TWO_FACTOR_REQUIRED"
    # Signing in and setting up 2FA stay reachable.
    assert (await member.get(ME)).status_code == 200
    assert (await member.get("/api/v1/auth/totp")).status_code == 200

    await enroll(member, clock)

    assert (await member.get(org_url(workspace.org_id))).status_code == 200
    assert (await member.get(f"/api/v1/projects/{workspace.project_id}")).status_code == 200


async def test_me_still_lists_the_org_for_a_locked_out_member(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    member = await totp_browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)

    memberships = (await member.get(ME)).json()["memberships"]

    assert [m["org"]["id"] for m in memberships] == [workspace.org_id]
    assert memberships[0]["org"]["require_2fa"] is True


async def test_the_check_comes_after_membership_so_strangers_still_get_404(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    stranger = await totp_browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)

    assert (await stranger.get(org_url(workspace.org_id))).status_code == 404
    assert (await stranger.get(f"/api/v1/projects/{workspace.project_id}")).status_code == 404


async def test_another_org_of_the_same_member_is_not_affected(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    member = await totp_browser_factory("member@example.com")
    strict = await create_workspace(owner, org_name="Strict")
    relaxed = await create_workspace(owner, org_name="Relaxed")
    await join_with_role(owner, member, strict.org_id, "member")
    await join_with_role(owner, member, relaxed.org_id, "member")
    await enroll(owner, clock)
    await require_two_factor(owner, strict.org_id)

    assert (await member.get(org_url(strict.org_id))).status_code == 403
    assert (await member.get(org_url(relaxed.org_id))).status_code == 200


async def test_an_owner_who_turns_their_own_two_factor_off_is_locked_out_of_their_org(
    totp_browser_factory: BrowserFactory, clock: FakeClock
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    secret, _ = await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)

    await owner.post("/api/v1/auth/totp/disable", json={"code": clock.next_code(secret)})

    assert (await owner.get(org_url(workspace.org_id))).status_code == 403
    # The way back is open: enrol again.
    await enroll(owner, clock)
    assert (await owner.get(org_url(workspace.org_id))).status_code == 200


async def test_api_keys_keep_ingesting_when_the_org_requires_two_factor(
    totp_browser_factory: BrowserFactory, totp_client: httpx.AsyncClient, clock: FakeClock
) -> None:
    # Two-factor authentication is about people signing in; an SDK has no second factor to give.
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await enroll(owner, clock)
    await require_two_factor(owner, workspace.org_id)

    response = await totp_client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id())]},
        headers=bearer(workspace.api_key),
    )

    assert response.status_code == 200, response.text


async def test_org_and_member_shapes_carry_the_flag(
    totp_browser_factory: BrowserFactory,
) -> None:
    owner = await totp_browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    created: Any = (await owner.post("/api/v1/orgs", json={"name": "Second"})).json()
    got = (await owner.get(org_url(workspace.org_id))).json()

    assert created["require_2fa"] is False
    assert got["require_2fa"] is False
