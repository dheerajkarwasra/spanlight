"""Deleting a project, with the traces, spans and keys that belong to it."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import BrowserFactory
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    count_project_rows,
    create_project,
    create_token,
    create_workspace,
    join_with_role,
    new_trace_id,
    span,
)

SessionFactory = async_sessionmaker[AsyncSession]


def project_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}"


async def ingest(client: httpx.AsyncClient, api_key: str, *, spans: int = 1) -> None:
    trace_id = new_trace_id()
    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=trace_id) for _ in range(spans)]},
        headers=bearer(api_key),
    )
    assert response.status_code == 200, response.text


async def audit_events(factory: SessionFactory, action: str) -> list[Any]:
    async with factory() as db:
        rows = await db.execute(
            text(
                "SELECT org_id, actor_user_id, target_type, target_id, metadata "
                "FROM audit_events WHERE action = :action ORDER BY created_at, id"
            ),
            {"action": action},
        )
        return list(rows.all())


async def project_exists(factory: SessionFactory, project_id: str) -> bool:
    async with factory() as db:
        found = await db.execute(text("SELECT 1 FROM projects WHERE id = :id"), {"id": project_id})
        return found.first() is not None


async def slug_of(browser: Browser, project_id: str) -> str:
    return str((await browser.get(project_url(project_id))).json()["slug"])


async def delete_project(who: Browser, project_id: str, confirm: object) -> httpx.Response:
    return await who.delete(
        project_url(project_id), json={"confirm": confirm, "password": "correct horse battery"}
    )


async def world(
    browser_factory: BrowserFactory, client: httpx.AsyncClient
) -> tuple[Browser, Browser, Workspace, str]:
    """An owner and an admin; a project with traces and a sibling project with traces."""
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    sibling = await create_project(owner, workspace.org_id, "Sibling")
    await join_with_role(owner, admin, workspace.org_id, "admin")
    sibling_key = (await owner.post(f"/api/v1/projects/{sibling}/keys", json={"name": "k"})).json()
    await ingest(client, workspace.api_key, spans=2)
    await ingest(client, workspace.api_key)
    await ingest(client, sibling_key["secret"])
    return owner, admin, workspace, sibling


async def test_an_admin_deletes_a_project_with_its_traces_spans_and_keys(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, admin, workspace, sibling = await world(browser_factory, client)
    await owner.post(f"/api/v1/projects/{workspace.project_id}/keys", json={"name": "second"})
    slug = await slug_of(admin, workspace.project_id)
    before = await count_project_rows(session_factory, workspace.project_id)
    assert before == {"traces": 2, "spans": 3, "api_keys": 2}
    sibling_before = await count_project_rows(session_factory, sibling)

    response = await delete_project(admin, workspace.project_id, slug)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert await count_project_rows(session_factory, workspace.project_id) == {
        "traces": 0,
        "spans": 0,
        "api_keys": 0,
    }
    assert not await project_exists(session_factory, workspace.project_id)
    assert (await admin.get(project_url(workspace.project_id))).status_code == 404
    listed = (await admin.get(f"/api/v1/orgs/{workspace.org_id}/projects")).json()
    assert [project["id"] for project in listed] == [sibling]
    # The sibling project, and the org, are untouched.
    assert await count_project_rows(session_factory, sibling) == sibling_before
    assert (await admin.get(f"/api/v1/orgs/{workspace.org_id}")).status_code == 200


async def test_the_key_of_a_deleted_project_is_refused(
    browser_factory: BrowserFactory, client: httpx.AsyncClient
) -> None:
    owner, _, workspace, _ = await world(browser_factory, client)
    slug = await slug_of(owner, workspace.project_id)
    await delete_project(owner, workspace.project_id, slug)

    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id())]},
        headers=bearer(workspace.api_key),
    )

    assert response.status_code == 401


async def test_deleting_is_audited_with_the_name_and_slug_and_the_event_outlives_the_project(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, admin, workspace, _ = await world(browser_factory, client)
    slug = await slug_of(admin, workspace.project_id)

    await delete_project(admin, workspace.project_id, slug)

    (event,) = await audit_events(session_factory, "project.delete")
    assert str(event.org_id) == workspace.org_id
    assert str(event.actor_user_id) == admin.user["id"]
    assert (event.target_type, event.target_id) == ("project", workspace.project_id)
    assert event.metadata == {"name": "Chatbot", "slug": slug}
    listed = (await owner.get(f"/api/v1/orgs/{workspace.org_id}/audit")).json()["items"]
    deleted = [item for item in listed if item["action"] == "project.delete"]
    assert [item["target_id"] for item in deleted] == [workspace.project_id]
    assert deleted[0]["metadata"] == {"name": "Chatbot", "slug": slug}


@pytest.mark.parametrize("confirm", ["", "wrong", "Chatbot", "CHATBOT", "chatbot ", " chatbot"])
async def test_a_wrong_confirmation_deletes_nothing_and_is_not_audited(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    confirm: str,
) -> None:
    # The slug is "chatbot": the name, other casings and padding are all wrong.
    owner, _, workspace, _ = await world(browser_factory, client)

    response = await delete_project(owner, workspace.project_id, confirm)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "CONFIRMATION_MISMATCH"
    assert await project_exists(session_factory, workspace.project_id)
    assert (await count_project_rows(session_factory, workspace.project_id))["traces"] == 2
    assert await audit_events(session_factory, "project.delete") == []


async def test_the_confirmation_is_that_of_the_project_in_the_path(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, _, workspace, sibling = await world(browser_factory, client)
    sibling_slug = await slug_of(owner, sibling)

    response = await delete_project(owner, workspace.project_id, sibling_slug)

    assert response.status_code == 422
    assert response.json()["code"] == "CONFIRMATION_MISMATCH"
    assert await project_exists(session_factory, workspace.project_id)
    assert await project_exists(session_factory, sibling)


@pytest.mark.parametrize("body", [{}, {"confirm": None}, {"confirm": 5}, {"confirm": "x" * 201}])
async def test_a_malformed_confirmation_body_is_a_validation_error(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    owner, _, workspace, _ = await world(browser_factory, client)

    response = await owner.delete(project_url(workspace.project_id), json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert (await owner.get(project_url(workspace.project_id))).status_code == 200


async def test_a_delete_without_a_body_is_a_validation_error(
    browser_factory: BrowserFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.delete(project_url(workspace.project_id))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_lower_roles_cannot_delete_a_project(
    browser_factory: BrowserFactory, session_factory: SessionFactory, role: str
) -> None:
    owner = await browser_factory("owner@example.com")
    other = await browser_factory("other@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, other, workspace.org_id, role)
    slug = await slug_of(owner, workspace.project_id)

    # Refused for the role before the confirmation is looked at.
    right = await delete_project(other, workspace.project_id, slug)
    wrong = await delete_project(other, workspace.project_id, "wrong")

    for response in (right, wrong):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
    assert await project_exists(session_factory, workspace.project_id)
    assert await audit_events(session_factory, "project.delete") == []


async def test_the_owner_can_delete_a_project(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)

    assert (await delete_project(owner, workspace.project_id, slug)).status_code == 204


async def test_a_stranger_cannot_delete_a_project(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    stranger = await browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)

    response = await delete_project(stranger, workspace.project_id, slug)

    assert response.status_code == 404
    assert (await owner.get(project_url(workspace.project_id))).status_code == 200


async def test_deleting_a_project_twice_is_a_404(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)
    await delete_project(owner, workspace.project_id, slug)

    again = await delete_project(owner, workspace.project_id, slug)

    assert again.status_code == 404


async def test_a_projects_of_the_demo_org_cannot_be_deleted(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE organizations SET is_demo = true WHERE id = :id"),
            {"id": workspace.org_id},
        )
        await db.commit()

    response = await delete_project(owner, workspace.project_id, slug)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert await project_exists(session_factory, workspace.project_id)
    assert await audit_events(session_factory, "project.delete") == []


async def test_no_access_token_can_delete_a_project(
    browser_factory: BrowserFactory,
    bare_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)
    read = (await create_token(owner, scope="read"))["token"]
    write = (await create_token(owner, scope="write"))["token"]
    url = project_url(workspace.project_id)

    refused = await bare_client.request("DELETE", url, json={"confirm": slug}, headers=bearer(read))
    assert refused.status_code == 403
    assert refused.json()["code"] == "SESSION_REQUIRED"
    assert await project_exists(session_factory, workspace.project_id)

    deleted = await bare_client.request(
        "DELETE", url, json={"confirm": slug}, headers=bearer(write)
    )
    assert deleted.status_code == 403
    assert deleted.json()["code"] == "SESSION_REQUIRED"
    assert await project_exists(session_factory, workspace.project_id)


async def test_an_api_key_cannot_delete_a_project(
    browser_factory: BrowserFactory, bare_client: httpx.AsyncClient
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)

    response = await bare_client.request(
        "DELETE",
        project_url(workspace.project_id),
        json={"confirm": slug},
        headers=bearer(workspace.api_key),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "KEY_SCOPE"


async def test_the_slug_of_a_deleted_project_can_be_used_again(
    browser_factory: BrowserFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = await slug_of(owner, workspace.project_id)
    await delete_project(owner, workspace.project_id, slug)

    again = await owner.post(f"/api/v1/orgs/{workspace.org_id}/projects", json={"name": "Chatbot"})

    assert again.status_code == 201
    assert again.json()["slug"] == slug
