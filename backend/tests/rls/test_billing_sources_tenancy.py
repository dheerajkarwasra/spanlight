"""Billing sources stay inside their organization (spec §12, program §4 "Billing data").

`billing_sources` is not under row-level security: every query filters `org_id`, and the
project mapping is held by composite foreign keys. Failure modes: another organization's owner
reads, edits, rotates or deletes a source by its id; a source is mapped to another
organization's project. The role-by-role matrix (viewer, member, admin, owner, outsider, API key,
personal access tokens) is `tests/authz` and `tests/keys/test_token_route_inventory.py`.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models.billing import BillingSourceProject
from tests.helpers import Browser, Workspace, create_workspace
from tests.reconciliation.sources_support import (
    ANTHROPIC_KEY_2,
    ProviderApi,
    answering,
    create_source,
    running_app,
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


class Tenants:
    def __init__(self, a: Browser, b: Browser, a_ws: Workspace, b_ws: Workspace) -> None:
        self.a, self.b, self.a_ws, self.b_ws = a, b, a_ws, b_ws
        self.source: dict[str, Any] = {}


@pytest.fixture
async def tenants(browser_factory: Any) -> Tenants:
    a: Browser = await browser_factory("a@example.com")
    b: Browser = await browser_factory("b@example.com")
    found = Tenants(a, b, await create_workspace(a), await create_workspace(b, org_name="B"))
    found.source = await create_source(a, found.a_ws.org_id, project_ids=[found.a_ws.project_id])
    return found


def attempts(org_id: str, source_id: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every route on one source, with a body the route accepts."""
    url = f"{sources_url(org_id)}/{source_id}"
    return [
        ("GET", url, None),
        ("PATCH", url, {"name": "Taken over"}),
        ("POST", f"{url}/rotate", {"admin_key": ANTHROPIC_KEY_2}),
        ("POST", f"{url}/refresh", None),
        ("POST", f"{url}/upload", None),
        ("GET", f"{url}/runs", None),
        ("DELETE", url, None),
    ]


async def send(browser: Browser, method: str, url: str, body: dict[str, Any] | None) -> int:
    if method == "GET":
        return (await browser.get(url)).status_code
    if method == "PATCH":
        return (await browser.patch(url, json=body)).status_code
    if method == "POST":
        return (await browser.post(url, json=body)).status_code
    return (await browser.delete(url)).status_code


async def test_another_orgs_owner_gets_404_on_every_route(
    tenants: Tenants, provider_api: ProviderApi
) -> None:
    source_id = tenants.source["id"]
    checks_before = len(provider_api.requests)
    # Through org A's path (not a member) and through B's own path (not B's source).
    for org_id in (tenants.a_ws.org_id, tenants.b_ws.org_id):
        for method, url, body in attempts(org_id, source_id):
            assert await send(tenants.b, method, url, body) == 404, (method, url)
    assert (await tenants.b.get(sources_url(tenants.a_ws.org_id))).status_code == 404
    create = await tenants.b.post(sources_url(tenants.a_ws.org_id), json=source_body(name="X"))
    assert create.status_code == 404

    assert (await tenants.b.get(sources_url(tenants.b_ws.org_id))).json() == []
    assert len(provider_api.requests) == checks_before  # no rotation reached the provider
    still = await tenants.a.get(f"{sources_url(tenants.a_ws.org_id)}/{source_id}")
    assert still.status_code == 200
    assert still.json() == tenants.source


async def test_a_project_of_another_org_is_422_on_create_and_patch(tenants: Tenants) -> None:
    theirs = tenants.b_ws.project_id
    create = await tenants.a.post(
        sources_url(tenants.a_ws.org_id),
        json=source_body("csv_upload", name="CSV", provider="openai", project_ids=[theirs]),
    )
    assert create.status_code == 422, create.text
    assert [error["field"] for error in create.json()["errors"]] == ["project_ids"]

    patch = await tenants.a.patch(
        f"{sources_url(tenants.a_ws.org_id)}/{tenants.source['id']}",
        json={"project_ids": [theirs]},
    )
    assert patch.status_code == 422, patch.text
    assert [error["field"] for error in patch.json()["errors"]] == ["project_ids"]


@pytest.mark.parametrize("mapping_org", ["source", "project"])
async def test_a_cross_org_mapping_cannot_be_written(
    tenants: Tenants, session_factory: async_sessionmaker[AsyncSession], mapping_org: str
) -> None:
    """Whichever organization the row claims, one of the composite foreign keys refuses it."""
    org_id = tenants.a_ws.org_id if mapping_org == "source" else tenants.b_ws.org_id
    async with session_factory() as db:
        db.add(
            BillingSourceProject(
                source_id=tenants.source["id"],
                project_id=tenants.b_ws.project_id,
                org_id=org_id,
                provider="anthropic",
            )
        )
        with pytest.raises(IntegrityError, match="foreign key"):
            await db.flush()
