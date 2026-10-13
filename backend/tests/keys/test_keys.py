"""API keys: creation, own/any revocation, and revoked keys losing access."""

from typing import Any

import httpx

from tests.helpers import Browser, bearer, create_workspace, join_with_role, new_trace_id, span


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
