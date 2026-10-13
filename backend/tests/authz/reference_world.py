"""Two organizations whose resources name each other: the world of the cross-org reference tests.

Organization A is the caller. Organization B holds the things A must not be able to name: an
alert channel, a provider credential and a project with a gateway key. A also has a second
project with a gateway key (same organization, other project) and a channel of its own, the
control that proves each body is valid apart from the id it names.
"""

from dataclasses import dataclass
from typing import Any

from tests.helpers import Browser, create_project, create_workspace

# A PagerDuty routing key is 32 letters and digits; nothing ever sends to it.
ROUTING_KEY = "a1" * 16
CREDENTIAL = {"name": "openai", "provider": "openai", "api_key": "sk-test-cross-org-reference"}


@dataclass
class World:
    a: Browser
    b: Browser
    a_org: str
    b_org: str
    a_project: str
    a_other_project: str
    b_project: str
    a_channel: str  # a channel of A
    b_channel: str  # a channel of B
    b_credential: str  # a credential of B
    a_credential: str  # a credential of A
    a_key: str  # a gateway key of A's project
    a_other_key: str  # a gateway key of A's other project
    b_key: str  # a gateway key of B's project


def route_config(*credential_ids: str) -> dict[str, Any]:
    """A route config with one target per credential and the default retry and fallback."""
    targets = [{"credential_id": credential_id} for credential_id in credential_ids]
    return {"targets": targets, "retry": {}, "fallback": {}}


async def created(browser: Browser, url: str, body: dict[str, Any]) -> str:
    response = await browser.post(url, json=body)
    assert response.status_code == 201, response.text
    identifier: str = response.json()["id"]
    return identifier


async def create_channel(browser: Browser, org_id: str, name: str) -> str:
    body = {"kind": "pagerduty", "name": name, "secret": ROUTING_KEY}
    return await created(browser, f"/api/v1/orgs/{org_id}/alert-channels", body)


async def create_key(browser: Browser, project_id: str, credential_id: str) -> str:
    """A gateway key; it needs a default route, so the project's first route is made here."""
    gateway = f"/api/v1/projects/{project_id}/gateway"
    route = {"name": "base", "config": route_config(credential_id)}
    await created(browser, f"{gateway}/routes", route)
    return await created(browser, f"{gateway}/keys", {"name": "key", "environment": "test"})


async def build_world(a: Browser, b: Browser) -> World:
    a_work = await create_workspace(a, org_name="Org A")
    b_work = await create_workspace(b, org_name="Org B")
    a_org, b_org = a_work.org_id, b_work.org_id
    a_other_project = await create_project(a, a_org, "Second")
    a_credential = await created(a, f"/api/v1/orgs/{a_org}/credentials", CREDENTIAL)
    b_credential = await created(b, f"/api/v1/orgs/{b_org}/credentials", CREDENTIAL)
    return World(
        a=a,
        b=b,
        a_org=a_org,
        b_org=b_org,
        a_project=a_work.project_id,
        a_other_project=a_other_project,
        b_project=b_work.project_id,
        a_channel=await create_channel(a, a_org, "a-oncall"),
        b_channel=await create_channel(b, b_org, "b-oncall"),
        b_credential=b_credential,
        a_credential=a_credential,
        a_key=await create_key(a, a_work.project_id, a_credential),
        a_other_key=await create_key(a, a_other_project, a_credential),
        b_key=await create_key(b, b_work.project_id, b_credential),
    )
