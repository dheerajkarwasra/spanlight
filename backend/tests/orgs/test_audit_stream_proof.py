"""Where the audit trail goes, and whether it goes anywhere, needs the account owner: a stolen
session must not send it elsewhere or silence the copy that would show what it did."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AuditStream, NotificationOutbox
from tests.conftest import BrowserFactory
from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import create_token, join_with_role, verify_email

STREAM = {"url": "https://1.1.1.1/audit"}


async def test_put_without_password_refused(gateway_workspace: GatewayWorkspace) -> None:
    owner = gateway_workspace.owner
    stream = f"/api/v1/orgs/{gateway_workspace.org_id}/audit/stream"

    stolen = await owner.put(stream, json=STREAM)

    assert stolen.status_code == 422, stolen.text
    assert stolen.json()["code"] == "INVALID_PASSWORD"
    token = (await create_token(owner))["token"]
    through_token = await owner.http.put(
        stream, json=STREAM, headers={"Authorization": f"Bearer {token}"}
    )
    assert through_token.status_code == 403, through_token.text
    assert through_token.json()["code"] == "SESSION_REQUIRED"
    assert (await owner.get(stream)).json() is None


async def test_password_sets_up_and_missing_proof_cannot_rotate_or_remove(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    path = f"/api/v1/orgs/{gateway_workspace.org_id}/audit/stream"
    await verify_email(session_factory, owner.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"
    created = await owner.put(path, json={**STREAM, "password": "correct horse battery"})
    assert created.status_code == 200, created.text
    assert created.json()["secret"]
    before = (await owner.get(path)).json()
    assert before["url"] == STREAM["url"]
    assert before["enabled"] is True
    async with session_factory() as db:
        secret_before = await db.scalar(select(AuditStream.secret_enc))

    rotated = await owner.post(f"{path}/rotate-secret", json={})
    assert rotated.status_code == 422, rotated.text
    assert rotated.json()["code"] == "INVALID_PASSWORD"
    assert (await owner.get(path)).json() == before
    removed = await owner.delete(path, json={})
    assert removed.status_code == 422, removed.text
    assert removed.json()["code"] == "INVALID_PASSWORD"
    assert (await owner.get(path)).json() == before
    async with session_factory() as db:
        assert await db.scalar(select(AuditStream.secret_enc)) == secret_before
        assert list(await db.scalars(select(NotificationOutbox))) == []


async def test_stream_change_notifies_the_other_verified_owner_not_the_actor(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    await join_with_role(owner, other, str(gateway_workspace.org_id), "owner")
    await verify_email(session_factory, owner.user["email"])
    await verify_email(session_factory, other.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"
    path = f"/api/v1/orgs/{gateway_workspace.org_id}/audit/stream"

    created = await owner.put(
        path,
        json={
            "url": "https://1.1.1.1/private-path?token=private-token",
            "password": "correct horse battery",
        },
    )

    assert created.status_code == 200, created.text
    async with session_factory() as db:
        (notice,) = list(await db.scalars(select(NotificationOutbox)))
    assert notice.target == {"to": other.user["email"]}
    assert "Gateway Org" in notice.payload["subject"]
    assert "Owner" in notice.payload["text"]
    assert "set up" in notice.payload["text"]
    assert "1.1.1.1" in notice.payload["text"]
    assert "If you weren't expecting this" in notice.payload["text"]
    assert "private-path" not in notice.payload["text"]
    assert "private-token" not in notice.payload["text"]
    assert "correct horse battery" not in notice.payload["text"]
    assert "<a " not in notice.payload["html"]


async def test_a_notice_says_what_changed_and_a_no_op_sends_none(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    browser_factory: BrowserFactory,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner = gateway_workspace.owner
    other = await browser_factory("other-owner@example.com")
    await join_with_role(owner, other, str(gateway_workspace.org_id), "owner")
    await verify_email(session_factory, owner.user["email"])
    await verify_email(session_factory, other.user["email"])
    gateway_settings.email_console_file = tmp_path / "email.jsonl"
    path = f"/api/v1/orgs/{gateway_workspace.org_id}/audit/stream"
    proof = {"password": "correct horse battery"}
    assert (await owner.put(path, json={**STREAM, **proof})).status_code == 200

    assert (await owner.put(path, json={**STREAM, **proof})).status_code == 200
    moved = {"url": "https://1.0.0.1/audit", "enabled": False, **proof}
    assert (await owner.put(path, json=moved)).status_code == 200

    async with session_factory() as db:
        notices = list(await db.scalars(select(NotificationOutbox)))
    assert len(notices) == 2  # set up, then the move; the unchanged PUT says nothing
    text = max(notices, key=lambda n: n.created_at).payload["text"]
    assert "URL changed" in text and "paused" in text


async def test_rotate_without_keys_refuses_before_a_code_is_spent(
    gateway_workspace: GatewayWorkspace, gateway_settings: Settings
) -> None:
    owner = gateway_workspace.owner
    path = f"/api/v1/orgs/{gateway_workspace.org_id}/audit/stream"
    assert (await owner.put(path, json={**STREAM, "password": "correct horse battery"})).is_success
    gateway_settings.credentials_keys = None

    response = await owner.post(f"{path}/rotate-secret", json={})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "NOT_CONFIGURED"
