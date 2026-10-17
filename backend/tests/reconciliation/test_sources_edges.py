"""Billing source writes at their edges: an unchecked rotation, a lost race, an empty edit.

Failure modes: a key rotated while provider calls are off keeps the old key's "ok", so an
unchecked key looks verified; another request takes the name or a project while the key is
being checked, and the create answers 500 instead of 409; an empty edit writes an audit event.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AuditEvent
from app.db.models.billing import BillingSource, BillingSourceProject
from app.reconciliation import service
from app.reconciliation.source_keys import KeyCheck
from tests.helpers import Browser, create_workspace
from tests.reconciliation.sources_support import (
    ANTHROPIC_KEY_2,
    ProviderApi,
    answering,
    create_source,
    running_app,
    signed_up,
    source_body,
    sources_url,
)


@pytest.fixture
def provider_api() -> ProviderApi:
    return answering()


@pytest.fixture
async def app(gateway_settings: Settings, provider_api: ProviderApi) -> AsyncIterator[Any]:
    async with running_app(gateway_settings, provider_api) as application:
        yield application


async def audit_count(session_factory: async_sessionmaker[AsyncSession], org_id: str) -> int:
    async with session_factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.org_id == org_id)
        )
    return int(count or 0)


async def test_rotate_with_provider_calls_off_resets_the_status(
    gateway_settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    api = answering()
    off = gateway_settings.model_copy(update={"billing_sources_enabled": False})
    async with running_app(off, api) as application:
        owner = await signed_up(application, "airgapped@example.com")
        try:
            workspace = await create_workspace(owner)
            created = await create_source(owner, workspace.org_id)
            # What an earlier check of the old key left behind.
            async with session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE billing_sources SET status = 'failing', "
                        "last_error = '401 Unauthorized', last_fetched_at = now() WHERE id = :id"
                    ),
                    {"id": created["id"]},
                )
                await db.commit()
            url = f"{sources_url(workspace.org_id)}/{created['id']}/rotate"
            rotated = await owner.post(url, json={"admin_key": ANTHROPIC_KEY_2})
        finally:
            await owner.http.aclose()

    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert (body["status"], body["last_error"]) == ("never_fetched", None)
    assert api.requests == []


@pytest.fixture
def taken_during_check(
    monkeypatch: pytest.MonkeyPatch, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    """While the key is being checked, another writer commits the row named in `race`."""
    race: dict[str, Any] = {}

    async def check_key(*_: Any, now: datetime, **__: Any) -> KeyCheck:
        async with session_factory() as db:
            racer = BillingSource(
                id=uuid.uuid4(),
                org_id=race["org_id"],
                kind="csv_upload",
                provider="anthropic",
                name=race["name"],
            )
            db.add(racer)
            await db.flush()
            for project_id in race.get("project_ids", []):
                db.add(
                    BillingSourceProject(
                        source_id=racer.id,
                        project_id=project_id,
                        org_id=race["org_id"],
                        provider="anthropic",
                    )
                )
            await db.commit()
        return KeyCheck("ok", None, now)

    monkeypatch.setattr(service, "check_key", check_key)
    return race


async def test_a_name_taken_during_the_check_is_409(
    browser_factory: Any,
    taken_during_check: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    taken_during_check.update(org_id=workspace.org_id, name="Prod account")
    audited = await audit_count(session_factory, workspace.org_id)

    response = await owner.post(sources_url(workspace.org_id), json=source_body())

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BILLING_SOURCE_NAME_TAKEN"
    assert await audit_count(session_factory, workspace.org_id) == audited


async def test_a_project_taken_during_the_check_is_409(
    browser_factory: Any,
    taken_during_check: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    taken_during_check.update(
        org_id=workspace.org_id, name="Racer", project_ids=[workspace.project_id]
    )
    audited = await audit_count(session_factory, workspace.org_id)

    response = await owner.post(
        sources_url(workspace.org_id), json=source_body(project_ids=[workspace.project_id])
    )

    assert response.status_code == 409, response.text
    problem = response.json()
    assert problem["code"] == "BILLING_SOURCE_PROJECT_TAKEN"
    assert "Racer" in problem["detail"]
    assert [error["field"] for error in problem["errors"]] == ["project_ids"]
    listed = await owner.get(sources_url(workspace.org_id))
    assert [source["name"] for source in listed.json()] == ["Racer"]
    assert await audit_count(session_factory, workspace.org_id) == audited


async def test_an_empty_patch_writes_nothing(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    created = await create_source(owner, workspace.org_id, project_ids=[workspace.project_id])
    audited = await audit_count(session_factory, workspace.org_id)

    response = await owner.patch(f"{sources_url(workspace.org_id)}/{created['id']}", json={})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == created
    assert await audit_count(session_factory, workspace.org_id) == audited
