"""A body may only name the caller's own channels, credentials and gateway keys.

`alert_rules.channel_ids`, `budgets.channel_ids`, `projects.insight_channel_ids` and a gateway
route's `credential_id`s are plain ids with no foreign key, and a budget's `scope_id` is text, so
nothing in the database stops one organization (or project) from naming another's. The services
check every id on create and on edit; these tests send ids that really exist, in another
organization, in otherwise valid bodies, so the check is the only thing that can refuse them. The
control in each test sends the caller's own id and succeeds.
"""

from typing import Any

import pytest

from tests.authz.reference_world import World, route_config

RULE = {
    "name": "slow",
    "kind": "threshold",
    "metric": "p95_ms",
    "comparator": "gt",
    "threshold": "5000",
    "window_minutes": 60,
}
BUDGET = {
    "name": "cap",
    "scope": "project",
    "period": "monthly",
    "amount_usd": "10",
    "action": "notify",
}


@pytest.fixture
async def app(gateway_app: Any) -> Any:
    """The app that `browser_factory` signs people in to: one that can seal credentials."""
    return gateway_app


def problem_fields(body: dict[str, Any]) -> list[str]:
    return [error["field"] for error in body.get("errors", [])]


async def test_a_rule_naming_another_orgs_channel_is_refused_on_create(world: World) -> None:
    base = f"/api/v1/projects/{world.a_project}/alert-rules"

    refused = await world.a.post(base, json={**RULE, "channel_ids": [world.b_channel]})

    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_CHANNEL"
    assert problem_fields(refused.json()) == ["channel_ids"]
    assert (await world.a.get(base)).json() == []
    own = await world.a.post(base, json={**RULE, "channel_ids": [world.a_channel]})
    assert own.status_code == 201, own.text


async def test_a_rule_edited_to_name_another_orgs_channel_is_refused(world: World) -> None:
    base = f"/api/v1/projects/{world.a_project}/alert-rules"
    rule = (await world.a.post(base, json={**RULE, "channel_ids": [world.a_channel]})).json()

    refused = await world.a.patch(
        f"{base}/{rule['id']}", json={**RULE, "channel_ids": [world.a_channel, world.b_channel]}
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_CHANNEL"
    stored = (await world.a.get(f"{base}/{rule['id']}")).json()
    assert stored["channel_ids"] == [world.a_channel]


async def test_a_budget_naming_another_orgs_channel_is_refused_on_create_and_edit(
    world: World,
) -> None:
    base = f"/api/v1/projects/{world.a_project}/budgets"

    refused = await world.a.post(base, json={**BUDGET, "channel_ids": [world.b_channel]})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_CHANNEL"
    assert (await world.a.get(base)).json() == []

    budget = await world.a.post(base, json={**BUDGET, "channel_ids": [world.a_channel]})
    assert budget.status_code == 201, budget.text
    refused = await world.a.patch(
        f"{base}/{budget.json()['id']}", json={"channel_ids": [world.b_channel]}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_CHANNEL"
    stored = (await world.a.get(f"{base}/{budget.json()['id']}")).json()
    assert stored["channel_ids"] == [world.a_channel]


async def test_a_projects_insight_channels_must_belong_to_its_org(world: World) -> None:
    url = f"/api/v1/projects/{world.a_project}"

    refused = await world.a.patch(url, json={"insight_channel_ids": [world.b_channel]})

    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_CHANNEL"
    assert (await world.a.get(url)).json()["insight_channel_ids"] == []
    own = await world.a.patch(url, json={"insight_channel_ids": [world.a_channel]})
    assert own.status_code == 200, own.text


@pytest.mark.parametrize("foreign", ["b_key", "a_other_key"])
async def test_a_gateway_key_budget_must_name_a_key_of_its_own_project(
    world: World, foreign: str
) -> None:
    """Another organization's key and another project's key (same org) are both refused."""
    base = f"/api/v1/projects/{world.a_project}/budgets"
    scoped = {**BUDGET, "scope": "gateway_key"}
    key_id = getattr(world, foreign)

    refused = await world.a.post(base, json={**scoped, "scope_id": key_id})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_SCOPE"
    assert (await world.a.get(base)).json() == []

    budget = await world.a.post(base, json={**scoped, "scope_id": world.a_key})
    assert budget.status_code == 201, budget.text
    refused = await world.a.patch(
        f"{base}/{budget.json()['id']}", json={"scope": "gateway_key", "scope_id": key_id}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "UNKNOWN_SCOPE"
    assert (await world.a.get(f"{base}/{budget.json()['id']}")).json()["scope_id"] == world.a_key


async def test_a_route_naming_another_orgs_credential_is_refused_on_create_and_edit(
    world: World,
) -> None:
    base = f"/api/v1/projects/{world.a_project}/gateway/routes"
    own, foreign = world.a_credential, world.b_credential

    refused = await world.a.post(base, json={"name": "main", "config": route_config(own, foreign)})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "VALIDATION_ERROR"
    assert problem_fields(refused.json()) == ["config.targets.1.credential_id"]
    assert [r["name"] for r in (await world.a.get(base)).json()] == ["base"]

    route = await world.a.post(base, json={"name": "main", "config": route_config(own)})
    assert route.status_code == 201, route.text
    refused = await world.a.put(
        f"{base}/{route.json()['id']}",
        json={"config": route_config(foreign), "expected_version": 1},
    )
    assert refused.status_code == 422, refused.text
    assert problem_fields(refused.json()) == ["config.targets.0.credential_id"]
    stored = (await world.a.get(f"{base}/{route.json()['id']}")).json()
    assert stored["version"] == 1
    assert stored["config"]["targets"][0]["credential_id"] == world.a_credential
