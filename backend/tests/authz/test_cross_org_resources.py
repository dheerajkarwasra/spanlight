"""An organization's credential, alert channel or price override is invisible to other orgs.

These resources hang off the organization, not a project, and are addressed by id. The matrix
sends ids that exist nowhere; here the ids are real and belong to another organization, with
bodies that are valid, so the only thing that can refuse the request is the tenancy check.
Another org's id must read as "not found" (404), never as "forbidden" (403), and the resource
must still be there afterwards. Credentials need `CREDENTIALS_KEYS`, so the app is the gateway
test app.
"""

from typing import Any

import pytest

from tests.helpers import Browser, create_workspace, join_with_role

CREDENTIAL = {"name": "openai", "provider": "openai", "api_key": "sk-test-cross-org-fixture"}
# A PagerDuty routing key is 32 letters and digits; nothing ever sends to it.
PAGERDUTY_ROUTING_KEY = "a1" * 16
OVERRIDE = {
    "provider": "openai",
    "model_pattern": "gpt-cross-org",
    "input_per_mtok": "1",
    "output_per_mtok": "2",
}


@pytest.fixture
async def app(gateway_app: Any) -> Any:
    """The app that `browser_factory` signs people in to: one that can seal credentials."""
    return gateway_app


class Orgs:
    """Organization A, with a credential, an alert channel and a price override, and a viewer."""

    def __init__(self, a: Browser, viewer: Browser, b: Browser, a_org: str, b_org: str) -> None:
        self.a, self.viewer, self.b = a, viewer, b
        self.a_org, self.b_org = a_org, b_org
        self.ids: dict[str, str] = {}

    def a_paths(self) -> dict[str, str]:
        base = f"/api/v1/orgs/{self.a_org}"
        return {
            "credential": f"{base}/credentials/{self.ids['credential']}",
            "channel": f"{base}/alert-channels/{self.ids['channel']}",
            "override": f"{base}/price-overrides/{self.ids['override']}",
        }


@pytest.fixture
async def orgs(browser_factory: Any) -> Orgs:
    a: Browser = await browser_factory("a@example.com")
    workspace = await create_workspace(a)
    viewer: Browser = await browser_factory("viewer@example.com")
    await join_with_role(a, viewer, workspace.org_id, "viewer")
    b: Browser = await browser_factory("b@example.com")
    other = await create_workspace(b, org_name="Other")
    found = Orgs(a, viewer, b, workspace.org_id, other.org_id)

    base = f"/api/v1/orgs/{workspace.org_id}"
    credential = await a.post(f"{base}/credentials", json=CREDENTIAL)
    channel = await a.post(
        f"{base}/alert-channels",
        json={"kind": "pagerduty", "name": "oncall", "secret": PAGERDUTY_ROUTING_KEY},
    )
    override = await a.post(f"{base}/price-overrides", json=OVERRIDE)
    for name, response in (
        ("credential", credential),
        ("channel", channel),
        ("override", override),
    ):
        assert response.status_code == 201, response.text
        found.ids[name] = response.json()["id"]
    return found


def attempts(org: str, ids: dict[str, str]) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every id-addressed call on those resources, with a body the route accepts."""
    base = f"/api/v1/orgs/{org}"
    credential, channel, override = ids["credential"], ids["channel"], ids["override"]
    return [
        ("POST", f"{base}/credentials/{credential}/rotate", {"api_key": "sk-test-replaced"}),
        ("POST", f"{base}/credentials/{credential}/check", None),
        ("DELETE", f"{base}/credentials/{credential}", None),
        ("GET", f"{base}/alert-channels/{channel}", None),
        ("PATCH", f"{base}/alert-channels/{channel}", {"name": "renamed"}),
        ("POST", f"{base}/alert-channels/{channel}/test", None),
        ("GET", f"{base}/alert-channels/{channel}/deliveries", None),
        ("DELETE", f"{base}/alert-channels/{channel}", None),
        ("DELETE", f"{base}/price-overrides/{override}", None),
    ]


async def call(browser: Browser, method: str, path: str, body: dict[str, Any] | None) -> int:
    token = browser.http.cookies.get("spl_csrf")
    headers = {"X-CSRF-Token": token} if token else {}
    response = await browser.http.request(method, path, headers=headers, json=body)
    return response.status_code


async def test_another_orgs_resource_by_id_through_my_own_org_is_404(orgs: Orgs) -> None:
    """Org B's owner may manage org B, so only the ids can be wrong: 404 for every one."""
    results = {
        f"{method} {path}": await call(orgs.b, method, path, body)
        for method, path, body in attempts(orgs.b_org, orgs.ids)
    }
    assert set(results.values()) == {404}, results
    await assert_untouched(orgs)


async def test_another_orgs_resource_through_its_own_org_path_is_404(orgs: Orgs) -> None:
    """Org B's owner naming org A in the path is a non-member: 404 for every one."""
    results = {
        f"{method} {path}": await call(orgs.b, method, path, body)
        for method, path, body in attempts(orgs.a_org, orgs.ids)
    }
    assert set(results.values()) == {404}, results
    await assert_untouched(orgs)


async def test_a_viewer_of_the_org_is_forbidden_not_hidden(orgs: Orgs) -> None:
    """A member who lacks the permission gets 403 on the real id: the resource is theirs to see."""
    writes = [item for item in attempts(orgs.a_org, orgs.ids) if item[0] != "GET"]
    results = {
        f"{method} {path}": await call(orgs.viewer, method, path, body)
        for method, path, body in writes
    }
    assert set(results.values()) == {403}, results
    await assert_untouched(orgs)


async def test_the_owner_reaches_the_same_calls_in_their_own_org(orgs: Orgs) -> None:
    """The control: the ids and bodies above are valid, so 404 above is the tenancy check."""
    paths = orgs.a_paths()
    assert await call(orgs.a, "GET", paths["channel"], None) == 200
    assert await call(orgs.a, "GET", f"{paths['channel']}/deliveries", None) == 200
    assert await call(orgs.a, "PATCH", paths["channel"], {"name": "renamed"}) == 200
    rotate = f"{paths['credential']}/rotate"
    assert await call(orgs.a, "POST", rotate, {"api_key": "sk-test-replaced"}) == 200
    assert await call(orgs.a, "DELETE", paths["override"], None) == 204
    # Last, because they remove what the calls above used. `check` and `test` are left out: they
    # would call out to the provider and the channel.
    assert await call(orgs.a, "DELETE", paths["channel"], None) == 204
    assert await call(orgs.a, "DELETE", paths["credential"], None) == 204


async def assert_untouched(orgs: Orgs) -> None:
    """Org A still holds all three, unchanged."""
    base = f"/api/v1/orgs/{orgs.a_org}"
    credentials = (await orgs.a.get(f"{base}/credentials")).json()
    channels = (await orgs.a.get(f"{base}/alert-channels")).json()
    overrides = (await orgs.a.get(f"{base}/price-overrides")).json()
    assert [item["id"] for item in credentials] == [orgs.ids["credential"]]
    assert credentials[0]["rotated_at"] is None
    assert [(item["id"], item["name"]) for item in channels] == [(orgs.ids["channel"], "oncall")]
    assert [item["id"] for item in overrides] == [orgs.ids["override"]]
