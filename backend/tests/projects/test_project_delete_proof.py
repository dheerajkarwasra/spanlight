"""Deleting a project needs the account owner: a stolen session or token must not wipe its traces,
and the owners hear of it, since an admin can do this and they would otherwise never see it."""

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


async def test_deleting_a_project_needs_proof(gateway_workspace: GatewayWorkspace) -> None:
    owner = gateway_workspace.owner
    url = f"/api/v1/projects/{gateway_workspace.project_id}"
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

    typo = await owner.delete(url, json={"confirm": "not-the-slug"})
    assert typo.json()["code"] == "CONFIRMATION_MISMATCH", typo.text

    deleted = await owner.delete(url, json={"confirm": slug, "password": PASSWORD})

    assert deleted.status_code == 204, deleted.text
    assert (await owner.get(url)).status_code == 404


async def test_deleting_a_project_tells_the_other_verified_owners(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: Sessions,
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    unverified = await browser_factory("unverified-owner@example.com")
    org_id = str(gateway_workspace.org_id)
    await join_with_role(owner, other, org_id, "owner")
    await join_with_role(owner, unverified, org_id, "owner")
    await verify_email(session_factory, owner.user["email"])
    await verify_email(session_factory, other.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"
    url = f"/api/v1/projects/{gateway_workspace.project_id}"
    project = (await owner.get(url)).json()

    deleted = await owner.delete(url, json={"confirm": project["slug"], "password": PASSWORD})

    assert deleted.status_code == 204, deleted.text
    async with session_factory() as db:
        (notice,) = list(await db.scalars(select(NotificationOutbox)))
    assert notice.target == {"to": other.user["email"]}
    assert project["name"] in notice.payload["text"]
    assert "Gateway Org" in notice.payload["text"]
    assert owner.user["email"] in notice.payload["text"]
    assert PASSWORD not in notice.payload["text"]


async def test_a_demo_project_is_refused_before_proof(
    gateway_workspace: GatewayWorkspace, session_factory: Sessions
) -> None:
    owner = gateway_workspace.owner
    url = f"/api/v1/projects/{gateway_workspace.project_id}"
    slug = (await owner.get(url)).json()["slug"]
    async with session_factory() as db:
        await db.execute(
            text("UPDATE organizations SET is_demo = true WHERE id = :id"),
            {"id": str(gateway_workspace.org_id)},
        )
        await db.commit()

    refused = await owner.delete(url, json={"confirm": slug})

    assert refused.status_code == 403, refused.text
