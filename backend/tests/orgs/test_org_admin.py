"""Renaming and deleting an organization."""

import asyncio
import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.deletion import delete_organization, lock_organization
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


def org_url(org_id: str) -> str:
    return f"/api/v1/orgs/{org_id}"


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


async def ingest(client: httpx.AsyncClient, api_key: str) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": [span(trace_id=new_trace_id())]}, headers=bearer(api_key)
    )
    assert response.status_code == 200, response.text


async def org_row(factory: SessionFactory, org_id: str) -> Any:
    async with factory() as db:
        return (
            await db.execute(
                text("SELECT name, slug, require_2fa FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
        ).one_or_none()


async def count(factory: SessionFactory, table: str, org_id: str) -> int:
    async with factory() as db:
        return (
            await db.execute(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :id"), {"id": org_id}
            )
        ).scalar_one()


async def make_demo(factory: SessionFactory, org_id: str) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE organizations SET is_demo = true WHERE id = :id"), {"id": org_id}
        )
        await db.commit()


# --- renaming ----------------------------------------------------------------------------------


async def test_an_admin_can_rename_the_org_and_it_is_audited(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    await join_with_role(owner, admin, workspace.org_id, "admin")
    before = (await owner.get(org_url(workspace.org_id))).json()

    response = await admin.patch(org_url(workspace.org_id), json={"name": "  Acme Labs "})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"id", "name", "slug", "is_demo", "require_2fa"}
    assert body["name"] == "Acme Labs"
    # The slug is the org's stable identifier (the delete confirmation names it): a rename keeps it.
    assert body["slug"] == before["slug"]
    assert (await owner.get(org_url(workspace.org_id))).json()["name"] == "Acme Labs"

    (event,) = await audit_events(session_factory, "org.update")
    assert str(event.org_id) == workspace.org_id
    assert str(event.actor_user_id) == admin.user["id"]
    assert (event.target_type, event.target_id) == ("org", workspace.org_id)
    assert event.metadata == {"name": {"from": "Acme", "to": "Acme Labs"}}


async def test_the_owner_can_rename_the_org_too(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.patch(org_url(workspace.org_id), json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_lower_roles_cannot_rename_the_org(
    browser_factory: BrowserFactory, session_factory: SessionFactory, role: str
) -> None:
    owner = await browser_factory("owner@example.com")
    other = await browser_factory("other@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    await join_with_role(owner, other, workspace.org_id, role)

    response = await other.patch(org_url(workspace.org_id), json={"name": "Hijacked"})

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert (await org_row(session_factory, workspace.org_id)).name == "Acme"
    assert await audit_events(session_factory, "org.update") == []


async def test_a_stranger_cannot_rename_the_org(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    stranger = await browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)

    response = await stranger.patch(org_url(workspace.org_id), json={"name": "Hijacked"})

    assert response.status_code == 404


async def test_renaming_to_the_current_name_changes_nothing_and_is_not_audited(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")

    same = await owner.patch(org_url(workspace.org_id), json={"name": "Acme"})
    padded = await owner.patch(org_url(workspace.org_id), json={"name": "  Acme  "})

    assert same.status_code == padded.status_code == 200
    assert same.json()["name"] == padded.json()["name"] == "Acme"
    assert await audit_events(session_factory, "org.update") == []


async def test_a_rename_alongside_an_unchanged_setting_audits_only_the_name(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")

    response = await owner.patch(
        org_url(workspace.org_id), json={"name": "Acme Labs", "require_2fa": False}
    )

    assert response.status_code == 200
    (event,) = await audit_events(session_factory, "org.update")
    assert event.metadata == {"name": {"from": "Acme", "to": "Acme Labs"}}


async def test_an_admin_cannot_change_a_security_setting_even_with_a_rename(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    # `name` needs `org:update` and `require_2fa` needs `org:security`; a request with both needs
    # both, and is refused whole rather than half applied.
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    await join_with_role(owner, admin, workspace.org_id, "admin")

    for body in (
        {"name": "Renamed", "require_2fa": True},
        {"name": "Renamed", "require_2fa": False},
    ):
        response = await admin.patch(org_url(workspace.org_id), json=body)
        assert response.status_code == 403, body
        assert response.json()["code"] == "FORBIDDEN"

    assert (await org_row(session_factory, workspace.org_id)).name == "Acme"
    assert await audit_events(session_factory, "org.update") == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": None},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": 7},
        {"require_2fa": None},
        {"require_2fa": "maybe"},
        {"slug": "other"},
        {"name": "Fine", "is_demo": True},
    ],
)
async def test_an_invalid_body_is_a_422(
    browser_factory: BrowserFactory, body: dict[str, Any]
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.patch(org_url(workspace.org_id), json=body)

    assert response.status_code == 422, body
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_the_demo_org_cannot_be_renamed(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    await make_demo(session_factory, workspace.org_id)

    response = await owner.patch(org_url(workspace.org_id), json={"name": "Renamed"})

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert (await org_row(session_factory, workspace.org_id)).name == "Acme"


async def test_an_access_token_renames_only_with_the_write_scope(
    browser_factory: BrowserFactory, bare_client: httpx.AsyncClient
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    read = (await create_token(owner, scope="read"))["token"]
    write = (await create_token(owner, scope="write"))["token"]

    refused = await bare_client.patch(
        org_url(workspace.org_id), json={"name": "Nope"}, headers=bearer(read)
    )
    renamed = await bare_client.patch(
        org_url(workspace.org_id), json={"name": "Yes"}, headers=bearer(write)
    )

    assert refused.status_code == 403
    assert refused.json()["code"] == "TOKEN_SCOPE"
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Yes"


# --- deleting ----------------------------------------------------------------------------------


async def build_org(
    browser_factory: BrowserFactory, client: httpx.AsyncClient
) -> tuple[Browser, Browser, Browser, Workspace, str]:
    """An org with two projects holding traces and keys, an admin and a member.

    Returns the owner, the admin, the member, the first workspace and the second project's id.
    """
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    member = await browser_factory("member@example.com")
    workspace = await create_workspace(owner, org_name="Acme")
    second = await create_project(owner, workspace.org_id, "Second")
    await join_with_role(owner, admin, workspace.org_id, "admin")
    await join_with_role(owner, member, workspace.org_id, "member")
    second_key = (await owner.post(f"/api/v1/projects/{second}/keys", json={"name": "k"})).json()
    await ingest(client, workspace.api_key)
    await ingest(client, second_key["secret"])
    return owner, admin, member, workspace, second


async def delete_org(who: Browser, org_id: str, confirm: object) -> httpx.Response:
    return await who.delete(org_url(org_id), json={"confirm": confirm})


async def test_the_owner_deletes_the_org_and_everything_in_it(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, admin, member, workspace, second = await build_org(browser_factory, client)
    other = await create_workspace(owner, org_name="Elsewhere")
    await ingest(client, other.api_key)
    await owner.post(f"/api/v1/orgs/{workspace.org_id}/invites", json={"role": "viewer"})
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
    assert await count(session_factory, "audit_events", workspace.org_id) > 0
    assert (await count_project_rows(session_factory, workspace.project_id))["traces"] == 1

    response = await delete_org(owner, workspace.org_id, slug)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert await org_row(session_factory, workspace.org_id) is None
    for table in ("projects", "memberships", "invites", "audit_events"):
        assert await count(session_factory, table, workspace.org_id) == 0, table
    for project_id in (workspace.project_id, second):
        assert await count_project_rows(session_factory, project_id) == {
            "traces": 0,
            "spans": 0,
            "api_keys": 0,
        }
    # Another org of the same owner is untouched.
    assert (await count_project_rows(session_factory, other.project_id))["traces"] == 1
    assert (await owner.get(org_url(other.org_id))).status_code == 200

    for person in (owner, admin, member):
        assert (await person.get(org_url(workspace.org_id))).status_code == 404
    # People are not deleted with the org: they just no longer belong to it.
    for person in (admin, member):
        me = (await person.get("/api/v1/auth/me")).json()
        assert me["memberships"] == []
    assert [m["org"]["id"] for m in (await owner.get("/api/v1/auth/me")).json()["memberships"]] == [
        other.org_id
    ]


async def test_the_keys_of_a_deleted_org_stop_working(
    browser_factory: BrowserFactory, client: httpx.AsyncClient
) -> None:
    owner, _, _, workspace, _ = await build_org(browser_factory, client)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]

    await delete_org(owner, workspace.org_id, slug)

    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id())]},
        headers=bearer(workspace.api_key),
    )
    assert response.status_code == 401


async def test_deleting_the_org_a_second_time_is_a_404(
    browser_factory: BrowserFactory, client: httpx.AsyncClient
) -> None:
    owner, _, _, workspace, _ = await build_org(browser_factory, client)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
    assert (await delete_org(owner, workspace.org_id, slug)).status_code == 204

    again = await delete_org(owner, workspace.org_id, slug)

    assert again.status_code == 404


@pytest.mark.parametrize("confirm", ["", "wrong", "ACME", "acme ", "Acme", " acme", "acme-2"])
async def test_a_wrong_confirmation_deletes_nothing(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    confirm: str,
) -> None:
    # The slug is "acme": the name, other casings and padding are all wrong.
    owner, _, _, workspace, second = await build_org(browser_factory, client)

    response = await delete_org(owner, workspace.org_id, confirm)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "CONFIRMATION_MISMATCH"
    assert (await org_row(session_factory, workspace.org_id)) is not None
    assert await count(session_factory, "projects", workspace.org_id) == 2
    assert (await count_project_rows(session_factory, second))["traces"] == 1
    assert await count(session_factory, "memberships", workspace.org_id) == 3


@pytest.mark.parametrize("body", [{}, {"confirm": None}, {"confirm": 5}, {"confirm": "x" * 201}])
async def test_a_malformed_confirmation_body_is_a_validation_error(
    browser_factory: BrowserFactory, body: dict[str, Any]
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.delete(org_url(workspace.org_id), json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert (await owner.get(org_url(workspace.org_id))).status_code == 200


async def test_a_delete_without_a_body_is_a_validation_error(
    browser_factory: BrowserFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    response = await owner.delete(org_url(workspace.org_id))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("role", ["admin", "member", "viewer"])
async def test_only_an_owner_can_delete_the_org(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    role: str,
) -> None:
    owner = await browser_factory("owner@example.com")
    other = await browser_factory("other@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, other, workspace.org_id, role)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]

    # Even with the right confirmation, and before the confirmation is looked at.
    right = await delete_org(other, workspace.org_id, slug)
    wrong = await delete_org(other, workspace.org_id, "wrong")

    for response in (right, wrong):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
    assert (await org_row(session_factory, workspace.org_id)) is not None


async def test_a_stranger_cannot_delete_the_org(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    stranger = await browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]

    response = await delete_org(stranger, workspace.org_id, slug)

    assert response.status_code == 404
    assert (await owner.get(org_url(workspace.org_id))).status_code == 200


async def test_the_demo_org_cannot_be_deleted(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
    await make_demo(session_factory, workspace.org_id)

    response = await delete_org(owner, workspace.org_id, slug)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert (await org_row(session_factory, workspace.org_id)) is not None


async def test_an_access_token_deletes_only_with_the_write_scope(
    browser_factory: BrowserFactory,
    bare_client: httpx.AsyncClient,
    session_factory: SessionFactory,
) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
    read = (await create_token(owner, scope="read"))["token"]
    write = (await create_token(owner, scope="write"))["token"]
    url = org_url(workspace.org_id)

    refused = await bare_client.request("DELETE", url, json={"confirm": slug}, headers=bearer(read))
    assert refused.status_code == 403
    assert refused.json()["code"] == "TOKEN_SCOPE"
    assert (await org_row(session_factory, workspace.org_id)) is not None

    deleted = await bare_client.request(
        "DELETE", url, json={"confirm": slug}, headers=bearer(write)
    )
    assert deleted.status_code == 204
    assert (await org_row(session_factory, workspace.org_id)) is None


def logged(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    # Structlog hands the stdlib handler the event dict itself as the record's message.
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == event
    ]


async def test_deleting_an_org_logs_what_was_removed_and_no_secrets(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    owner, _, _, workspace, second = await build_org(browser_factory, client)
    slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
    wrong = await delete_org(owner, workspace.org_id, "wrong")
    assert wrong.status_code == 422

    await delete_org(owner, workspace.org_id, slug)

    # Only the delete that happened is logged, once.
    (event,) = logged(caplog, "org_deleted")
    assert event["org_id"] == workspace.org_id
    assert event["actor_user_id"] == owner.user["id"]
    assert sorted(event["project_ids"]) == sorted([workspace.project_id, second])
    # Ids and the request id, nothing else: no names, slugs or credentials.
    assert set(event) - {"level", "log_level", "timestamp", "logger"} == {
        "event",
        "org_id",
        "project_ids",
        "actor_user_id",
        "request_id",
    }
    # Ids only: neither a key, nor a credential, nor the confirmation text.
    assert workspace.api_key not in caplog.text
    assert "correct horse battery" not in caplog.text
    assert "spl_session" not in caplog.text


async def test_a_refused_delete_logs_nothing(
    browser_factory: BrowserFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")

    await delete_org(admin, workspace.org_id, "acme")
    await delete_org(owner, workspace.org_id, "wrong")

    assert logged(caplog, "org_deleted") == []


async def test_deleting_an_org_while_one_of_its_projects_is_deleted_does_not_deadlock(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    # Both deletes lock the organization before its project, so neither can hold the one the
    # other is waiting for. A deadlock shows up as a 500 (or an exception) for one of the two
    # requests, so the race is run several times: it does not happen on every round.
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    for round_number in range(15):
        workspace = await create_workspace(owner, org_name=f"Round {round_number}")
        await join_with_role(owner, admin, workspace.org_id, "admin")
        org_slug = (await owner.get(org_url(workspace.org_id))).json()["slug"]
        project_slug = (await admin.get(f"/api/v1/projects/{workspace.project_id}")).json()["slug"]

        org_result, project_result = await asyncio.gather(
            delete_org(owner, workspace.org_id, org_slug),
            admin.delete(
                f"/api/v1/projects/{workspace.project_id}", json={"confirm": project_slug}
            ),
        )

        # The org delete always succeeds. The project delete either got in first (204) or found
        # the org already gone (404); the two never leave anything half deleted.
        assert org_result.status_code == 204, (round_number, org_result.text)
        assert project_result.status_code in {204, 404}, (round_number, project_result.text)
        assert await org_row(session_factory, workspace.org_id) is None
        assert await count_project_rows(session_factory, workspace.project_id) == {
            "traces": 0,
            "spans": 0,
            "api_keys": 0,
        }


async def test_deleting_an_org_locks_its_projects_in_id_order_first(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    # The budget reaper takes `KEY SHARE` on projects in id order; the deletion locks them in the
    # same order before its DELETE, which would otherwise lock them in scan order.
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await create_project(owner, workspace.org_id, "Second")
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    async with session_factory() as db:
        assert await lock_organization(db, uuid.UUID(workspace.org_id)) is not None
        connection = (await db.connection()).sync_connection
        assert connection is not None
        event.listen(connection, "before_cursor_execute", record)
        try:
            deleted = await delete_organization(db, uuid.UUID(workspace.org_id))
        finally:
            event.remove(connection, "before_cursor_execute", record)
        await db.commit()

    assert len(deleted) == 2
    locks = [
        i
        for i, s in enumerate(statements)
        if "FROM projects" in s and "ORDER BY projects.id" in s and "FOR UPDATE" in s
    ]
    delete = next(i for i, s in enumerate(statements) if s.startswith("DELETE FROM projects"))
    assert locks and locks[0] < delete, "\n".join(statements)
