"""API keys: creation, own/any revocation, and revoked keys losing access."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import keys as keys_api
from app.db.rls import bypass_rls
from tests.helpers import (
    Browser,
    bearer,
    create_api_key,
    create_token,
    create_workspace,
    join_with_role,
    new_trace_id,
    span,
)


async def test_key_secret_shown_once_and_listed_without_secret(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    assert workspace.api_key.startswith("spl_live_")

    keys = (await owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    assert len(keys) == 1
    assert "secret" not in keys[0]
    assert workspace.api_key.startswith(keys[0]["prefix"] + "_")
    assert keys[0]["created_by"]["email"] == "owner@example.com"


async def test_revoked_key_is_rejected(browser_factory: Any, client: httpx.AsyncClient) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    batch = {"spans": [span(trace_id=new_trace_id())]}

    first = await client.post("/v1/traces", json=batch, headers=bearer(workspace.api_key))
    assert first.status_code == 200
    key_id = (await owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()[0]["id"]
    keys_after_use = (await owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    assert keys_after_use[0]["last_used_at"] is not None

    revoke = await owner.delete(f"/api/v1/projects/{workspace.project_id}/keys/{key_id}")
    assert revoke.status_code == 204
    second = await client.post("/v1/traces", json=batch, headers=bearer(workspace.api_key))
    assert second.status_code == 401
    assert second.json()["code"] == "UNAUTHORIZED"


async def test_wrong_secret_with_valid_prefix_is_rejected(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    prefix = workspace.api_key.rsplit("_", 1)[0]
    forged = f"{prefix}_{'a' * 32}"
    response = await client.post("/v1/traces", json={"spans": []}, headers=bearer(forged))
    assert response.status_code == 401


async def test_member_revokes_only_own_keys_admin_revokes_any(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    admin: Browser = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    await join_with_role(owner, admin, workspace.org_id, "admin")
    keys_url = f"/api/v1/projects/{workspace.project_id}/keys"

    owners_key = (await owner.get(keys_url)).json()[0]["id"]
    members_key = (await member.post(keys_url, json={"name": "mine"})).json()["id"]

    assert (await member.delete(f"{keys_url}/{owners_key}")).status_code == 403
    assert (await member.delete(f"{keys_url}/{members_key}")).status_code == 204
    assert (await admin.delete(f"{keys_url}/{owners_key}")).status_code == 204

    revoked = {key["id"]: key["revoked_at"] for key in (await owner.get(keys_url)).json()}
    assert revoked[owners_key] is not None and revoked[members_key] is not None


async def test_cannot_revoke_key_of_another_project(browser_factory: Any) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    first = await create_workspace(owner, org_name="One")
    second = await create_workspace(owner, org_name="Two")
    other_key = (await owner.get(f"/api/v1/projects/{second.project_id}/keys")).json()[0]["id"]

    response = await owner.delete(f"/api/v1/projects/{first.project_id}/keys/{other_key}")
    assert response.status_code == 404


async def test_every_api_key_query_filters_by_project(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    # `api_keys` has no row-level security (ADR 0003, "Exception: api_keys"), so the list and the
    # revoke queries are the only thing between one project's keys and another's.
    owner: Browser = await browser_factory("owner@example.com")
    first = await create_workspace(owner, org_name="One")
    second = await create_workspace(owner, org_name="Two")
    first_keys = (await owner.get(f"/api/v1/projects/{first.project_id}/keys")).json()
    second_keys = (await owner.get(f"/api/v1/projects/{second.project_id}/keys")).json()
    assert len(first_keys) == 1 and len(second_keys) == 1
    assert first_keys[0]["id"] != second_keys[0]["id"]

    revoke = await owner.delete(f"/api/v1/projects/{first.project_id}/keys/{second_keys[0]['id']}")
    assert revoke.status_code == 404
    batch = {"spans": [span(trace_id=new_trace_id())]}
    still_live = await client.post("/v1/traces", json=batch, headers=bearer(second.api_key))
    assert still_live.status_code == 200


async def test_read_scope_key_needs_owner_proof(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    keys = f"/api/v1/projects/{workspace.project_id}/keys"

    stolen = await owner.post(keys, json={"name": "read", "scopes": ["traces:read"]})

    assert stolen.status_code == 422, stolen.text
    assert stolen.json()["code"] == "INVALID_PASSWORD"
    token = (await create_token(owner))["token"]
    through_token = await owner.http.post(
        keys,
        json={"name": "read", "scopes": ["prompts:read"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert through_token.status_code == 403, through_token.text
    assert through_token.json()["code"] == "SESSION_REQUIRED"
    async with session_factory() as db:
        await bypass_rls(db)
        names = (await db.scalars(text("SELECT name FROM api_keys"))).all()
    assert "read" not in names


async def test_removing_a_member_strips_read_from_the_keys_they_created(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    member = await browser_factory("member@example.com")
    await join_with_role(owner, member, workspace.org_id, "member")
    keys = f"/api/v1/projects/{workspace.project_id}/keys"
    proof = {"password": "correct horse battery"}

    read_only = await member.post(keys, json={"name": "r", "scopes": ["traces:read"], **proof})
    mixed = await member.post(
        keys, json={"name": "m", "scopes": ["ingest:write", "traces:read"], **proof}
    )
    ingest = await member.post(keys, json={"name": "i", "scopes": ["ingest:write"]})
    assert read_only.status_code == mixed.status_code == ingest.status_code == 201

    removed = await owner.delete(
        f"/api/v1/orgs/{workspace.org_id}/members/{member.user['id']}",
        json={"password": "correct horse battery"},
    )
    assert removed.status_code == 204, removed.text

    listed = {k["name"]: k for k in (await owner.get(keys)).json()}
    assert listed["r"]["revoked_at"] is not None
    assert listed["m"]["revoked_at"] is None and listed["m"]["scopes"] == ["ingest:write"]
    assert listed["i"]["revoked_at"] is None and listed["i"]["scopes"] == ["ingest:write"]
    batch = {"spans": [span(trace_id=new_trace_id())]}
    sent = await client.post("/v1/traces", json=batch, headers=bearer(ingest.json()["secret"]))
    assert sent.status_code == 200, sent.text
    sent = await client.post("/v1/traces", json=batch, headers=bearer(mixed.json()["secret"]))
    assert sent.status_code == 200, sent.text
    async with session_factory() as db:
        await bypass_rls(db)
        actions = (
            await db.scalars(
                text(
                    "SELECT action::text FROM audit_events "
                    "WHERE action::text IN ('key.revoke', 'key.scopes_change')"
                )
            )
        ).all()
    assert sorted(actions) == ["key.revoke", "key.scopes_change"]


async def test_leaving_strips_read_keys_and_evicts_cached_access(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    owner = await browser_factory("owner@example.com")
    member = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    other = await create_workspace(member, org_name="Other")
    await join_with_role(owner, member, workspace.org_id, "member")
    reader = await create_api_key(member, workspace.project_id, scopes=["traces:read"])
    mixed = await create_api_key(
        member,
        workspace.project_id,
        scopes=["scores:write", "prompts:read", "ingest:write", "traces:read"],
    )
    ingest = await create_api_key(member, workspace.project_id)
    other_reader = await create_api_key(member, other.project_id, scopes=["traces:read"])
    traces = f"/api/v1/projects/{workspace.project_id}/traces"
    members = f"/api/v1/orgs/{workspace.org_id}/members"
    assert (await client.get(traces, headers=bearer(reader["secret"]))).status_code == 200
    assert (await client.get(traces, headers=bearer(mixed["secret"]))).status_code == 200
    listed_members = {m["user"]["id"]: m for m in (await owner.get(members)).json()}
    assert listed_members[member.user["id"]]["read_keys"] == 2
    assert listed_members[owner.user["id"]]["read_keys"] == 0
    updated = await owner.patch(f"{members}/{member.user['id']}", json={"role": "member"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["read_keys"] == 2

    left = await member.delete(f"{members}/{member.user['id']}")

    assert left.status_code == 204, left.text
    audit = await owner.get(f"/api/v1/orgs/{workspace.org_id}/audit?action=member.remove")
    assert audit.status_code == 200, audit.text
    assert audit.json()["items"][0]["metadata"] == {
        "role": "member",
        "revoked_keys": 1,
        "narrowed_keys": 1,
    }
    assert (await client.get(traces, headers=bearer(reader["secret"]))).status_code == 401
    refused = await client.get(traces, headers=bearer(mixed["secret"]))
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "KEY_SCOPE"
    listed = {
        k["id"]: k
        for k in (await owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    }
    assert listed[reader["id"]]["revoked_at"] is not None
    assert listed[mixed["id"]]["revoked_at"] is None
    assert listed[mixed["id"]]["scopes"] == ["scores:write", "ingest:write"]
    assert listed[ingest["id"]]["revoked_at"] is None
    assert listed[ingest["id"]]["scopes"] == ["ingest:write"]
    assert (
        await client.get(
            f"/api/v1/projects/{other.project_id}/traces", headers=bearer(other_reader["secret"])
        )
    ).status_code == 200
    batch = {"spans": [span(trace_id=new_trace_id())]}
    assert (
        await client.post("/v1/traces", json=batch, headers=bearer(mixed["secret"]))
    ).status_code == 200
    assert (
        await client.post("/v1/traces", json=batch, headers=bearer(ingest["secret"]))
    ).status_code == 200


async def test_a_member_removed_while_creating_a_key_gets_no_key(
    browser_factory: Any,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    member = await browser_factory("member@example.com")
    await join_with_role(owner, member, workspace.org_id, "member")
    real_lock = keys_api.lock_project_for_write

    # The removal commits while `create_key` waits for the org lock, after its membership check.
    async def removed_while_waiting(db: AsyncSession, org_id: Any, project_id: Any) -> Any:
        async with session_factory() as other, other.begin():
            await bypass_rls(other)
            await other.execute(
                text("DELETE FROM memberships WHERE user_id = :id"), {"id": member.user["id"]}
            )
        return await real_lock(db, org_id, project_id)

    monkeypatch.setattr(keys_api, "lock_project_for_write", removed_while_waiting)
    response = await member.post(
        f"/api/v1/projects/{workspace.project_id}/keys",
        json={"name": "late", "scopes": ["traces:read"], "password": "correct horse battery"},
    )

    assert response.status_code == 404, response.text
    async with session_factory() as db:
        await bypass_rls(db)
        names = (await db.scalars(text("SELECT name FROM api_keys"))).all()
    assert "late" not in names
