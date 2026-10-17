"""Promoted metadata keys, set through `PATCH /api/v1/projects/{project_id}` (spec §5.1)."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import BrowserFactory
from tests.helpers import Browser, Workspace, create_workspace, join_with_role

SessionFactory = async_sessionmaker[AsyncSession]


def project_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}"


async def promote(who: Browser, project_id: str, keys: object) -> httpx.Response:
    return await who.patch(project_url(project_id), json={"promoted_metadata_keys": keys})


async def admin_world(browser_factory: BrowserFactory) -> tuple[Browser, Workspace]:
    owner = await browser_factory("owner@example.com")
    admin = await browser_factory("admin@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")
    return admin, workspace


async def update_audits(factory: SessionFactory) -> list[dict[str, Any]]:
    async with factory() as db:
        rows = await db.execute(
            text(
                "SELECT metadata FROM audit_events WHERE action = 'project.update' "
                "ORDER BY created_at, id"
            )
        )
        return [row.metadata for row in rows.all()]


async def test_admin_sets_up_to_five_promoted_keys_in_order_with_repeats_dropped(
    browser_factory: BrowserFactory,
) -> None:
    admin, workspace = await admin_world(browser_factory)
    keys = ["customer", "feature", "customer", "team.a-b", "run_kind", "feature", "plan"]

    response = await promote(admin, workspace.project_id, keys)

    assert response.status_code == 200, response.text
    expected = ["customer", "feature", "team.a-b", "run_kind", "plan"]
    assert response.json()["promoted_metadata_keys"] == expected
    stored = (await admin.get(project_url(workspace.project_id))).json()
    assert stored["promoted_metadata_keys"] == expected

    cleared = await promote(admin, workspace.project_id, [])

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["promoted_metadata_keys"] == []


@pytest.mark.parametrize(
    "keys",
    [
        ["a", "b", "c", "d", "e", "f"],
        ["Customer"],
        ["has space"],
        ["x" * 65],
        [""],
        ["a,b"],
        [5],
        "customer",
        [None],
    ],
)
async def test_six_keys_or_a_bad_pattern_is_422(
    browser_factory: BrowserFactory, keys: object
) -> None:
    admin, workspace = await admin_world(browser_factory)
    await promote(admin, workspace.project_id, ["kept"])

    response = await promote(admin, workspace.project_id, keys)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    stored = (await admin.get(project_url(workspace.project_id))).json()
    assert stored["promoted_metadata_keys"] == ["kept"]


async def test_six_distinct_keys_name_the_limit(browser_factory: BrowserFactory) -> None:
    admin, workspace = await admin_world(browser_factory)

    response = await promote(admin, workspace.project_id, ["a", "b", "c", "d", "e", "f"])

    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["field"] == "promoted_metadata_keys"
    assert "at most 5 promoted keys" in error["message"]


@pytest.mark.parametrize("role", ["viewer", "member"])
async def test_viewer_and_member_get_403(browser_factory: BrowserFactory, role: str) -> None:
    owner = await browser_factory("owner@example.com")
    other = await browser_factory(f"{role}@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, other, workspace.org_id, role)

    response = await promote(other, workspace.project_id, ["customer"])

    assert response.status_code == 403, response.text
    stored = (await owner.get(project_url(workspace.project_id))).json()
    assert stored["promoted_metadata_keys"] == []


async def test_audit_metadata_holds_old_and_new_key_names(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    admin, workspace = await admin_world(browser_factory)
    await promote(admin, workspace.project_id, ["customer", "feature"])

    await promote(admin, workspace.project_id, ["feature", "plan", "feature"])
    await admin.patch(project_url(workspace.project_id), json={"retention_days": 7})

    first, second, third = await update_audits(session_factory)
    assert first == {
        "changes": {"promoted_metadata_keys": ["customer", "feature"]},
        "previous": {"promoted_metadata_keys": []},
    }
    assert second == {
        "changes": {"promoted_metadata_keys": ["feature", "plan"]},
        "previous": {"promoted_metadata_keys": ["customer", "feature"]},
    }
    # Another field changing leaves the promoted keys out of the event.
    assert third == {"changes": {"retention_days": 7}}


async def test_sending_the_same_keys_again_records_no_previous_list(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    admin, workspace = await admin_world(browser_factory)
    await promote(admin, workspace.project_id, ["customer"])

    await promote(admin, workspace.project_id, ["customer", "customer"])

    _, repeat = await update_audits(session_factory)
    assert repeat == {"changes": {"promoted_metadata_keys": ["customer"]}}


async def test_project_out_returns_the_list(browser_factory: BrowserFactory) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)

    fresh = (await owner.get(project_url(workspace.project_id))).json()
    assert fresh["promoted_metadata_keys"] == []

    await promote(owner, workspace.project_id, ["customer"])

    listed = (await owner.get(f"/api/v1/orgs/{workspace.org_id}/projects")).json()
    assert [project["promoted_metadata_keys"] for project in listed] == [["customer"]]
