"""Removing someone else and deleting an organization need the account owner: a stolen session must
not lock the other owners out or wipe the audit trail. Leaving stays free: it grants nothing."""

from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import NotificationOutbox
from tests.conftest import BrowserFactory
from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import create_token, join_with_role, verify_email

PASSWORD = "correct horse battery"
Sessions = async_sessionmaker[AsyncSession]


async def _notices(sessions: Sessions) -> dict[str, str]:
    async with sessions() as db:
        rows = await db.scalars(select(NotificationOutbox))
        return {row.target["to"]: row.payload["text"] for row in rows}


async def test_removing_another_member_needs_proof(
    gateway_workspace: GatewayWorkspace, browser_factory: BrowserFactory
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    org = f"/api/v1/orgs/{gateway_workspace.org_id}"
    await join_with_role(owner, other, str(gateway_workspace.org_id), "owner")
    url = f"{org}/members/{other.user['id']}"

    stolen = await owner.delete(url)

    assert stolen.status_code == 422, stolen.text
    assert stolen.json()["code"] == "INVALID_PASSWORD"
    token = (await create_token(owner))["token"]
    through_token = await owner.http.request(
        "DELETE", url, headers={"Authorization": f"Bearer {token}"}
    )
    assert through_token.status_code == 403, through_token.text
    assert through_token.json()["code"] == "SESSION_REQUIRED"
    assert len((await owner.get(f"{org}/members")).json()) == 2

    removed = await owner.delete(url, json={"password": PASSWORD})

    assert removed.status_code == 204, removed.text
    assert len((await owner.get(f"{org}/members")).json()) == 1


async def test_deleting_an_organization_needs_proof(gateway_workspace: GatewayWorkspace) -> None:
    owner = gateway_workspace.owner
    url = f"/api/v1/orgs/{gateway_workspace.org_id}"
    slug = (await owner.get(url)).json()["slug"]

    stolen = await owner.delete(url, json={"confirm": slug})

    assert stolen.status_code == 422, stolen.text
    assert stolen.json()["code"] == "INVALID_PASSWORD"
    token = (await create_token(owner))["token"]
    through_token = await owner.http.request(
        "DELETE", url, json={"confirm": slug}, headers={"Authorization": f"Bearer {token}"}
    )
    assert through_token.status_code == 403, through_token.text
    assert through_token.json()["code"] == "SESSION_REQUIRED"
    assert (await owner.get(url)).status_code == 200

    deleted = await owner.delete(url, json={"confirm": slug, "password": PASSWORD})

    assert deleted.status_code == 204, deleted.text
    assert (await owner.get(url)).status_code == 404


async def test_a_doomed_request_is_refused_before_proof(
    gateway_workspace: GatewayWorkspace,
    browser_factory: BrowserFactory,
) -> None:
    owner = gateway_workspace.owner
    admin = await browser_factory("admin@example.com")
    org = f"/api/v1/orgs/{gateway_workspace.org_id}"
    await join_with_role(owner, admin, str(gateway_workspace.org_id), "admin")

    kick_owner = await admin.delete(f"{org}/members/{owner.user['id']}")
    typo = await owner.delete(org, json={"confirm": "not-the-slug"})

    assert kick_owner.status_code == 403, kick_owner.text
    assert typo.json()["code"] == "CONFIRMATION_MISMATCH", typo.text


async def test_removal_tells_the_other_owners_and_the_removed_person(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    target = await browser_factory("target@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(owner, other, org_id, "owner")
    await join_with_role(owner, target, org_id, "member")
    await verify_email(session_factory, owner.user["email"])
    await verify_email(session_factory, other.user["email"])
    await verify_email(session_factory, target.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"

    removed = await owner.delete(
        f"/api/v1/orgs/{org_id}/members/{target.user['id']}", json={"password": PASSWORD}
    )

    assert removed.status_code == 204, removed.text
    notices = await _notices(session_factory)
    # The actor is not told.
    assert set(notices) == {other.user["email"], target.user["email"]}
    assert target.user["email"] in notices[other.user["email"]]
    assert owner.user["email"] in notices[other.user["email"]]
    assert PASSWORD not in notices[other.user["email"]]


async def test_leaving_needs_no_proof_and_the_owners_hear_of_it(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
    tmp_path: Path,
) -> None:
    # The owner of the mail-enabled test app leaves: `browser_factory` signs in to another app.
    leaver = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(leaver, other, org_id, "owner")
    await verify_email(session_factory, other.user["email"])
    await verify_email(session_factory, leaver.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"

    left = await leaver.delete(f"/api/v1/orgs/{org_id}/members/{leaver.user['id']}")

    assert left.status_code == 204, left.text
    notices = await _notices(session_factory)
    # A stolen session could make someone leave: they hear of it too.
    assert set(notices) == {other.user["email"], leaver.user["email"]}
    assert leaver.user["email"] in notices[other.user["email"]]


async def test_an_unverified_address_is_never_mailed_on_removal(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
    tmp_path: Path,
) -> None:
    # Anyone can sign up with a stranger's address, join their own org and be removed from it:
    # mailing that address would relay the org's name to the stranger, again and again.
    owner = gateway_workspace.owner
    stranger = await browser_factory("stranger@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(owner, stranger, org_id, "member")
    gateway_settings.email_console_file = tmp_path / "email.jsonl"

    removed = await owner.delete(
        f"/api/v1/orgs/{org_id}/members/{stranger.user['id']}", json={"password": PASSWORD}
    )

    assert removed.status_code == 204, removed.text
    assert await _notices(session_factory) == {}


async def test_deleting_tells_the_other_owners_after_the_org_is_gone(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(owner, other, org_id, "owner")
    await verify_email(session_factory, owner.user["email"])
    await verify_email(session_factory, other.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"
    url = f"/api/v1/orgs/{org_id}"
    slug = (await owner.get(url)).json()["slug"]

    deleted = await owner.delete(url, json={"confirm": slug, "password": PASSWORD})

    assert deleted.status_code == 204, deleted.text
    notices = await _notices(session_factory)
    assert set(notices) == {other.user["email"]}
    assert "Gateway Org" in notices[other.user["email"]]
    assert owner.user["email"] in notices[other.user["email"]]


async def test_the_demo_membership_cannot_be_left(
    gateway_workspace: GatewayWorkspace,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
) -> None:
    owner = gateway_workspace.owner
    visitor = await browser_factory("visitor@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(owner, visitor, org_id, "viewer")
    async with session_factory() as db:
        await db.execute(
            text("UPDATE organizations SET is_demo = true WHERE id = :id"), {"id": org_id}
        )
        await db.commit()

    left = await visitor.delete(f"/api/v1/orgs/{org_id}/members/{visitor.user['id']}")

    assert left.status_code == 403, left.text
