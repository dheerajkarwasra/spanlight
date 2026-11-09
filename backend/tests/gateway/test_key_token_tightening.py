"""Write tokens can still restrict an existing gateway key."""

import httpx

from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import bearer, create_token


async def test_write_token_can_tighten_a_gateway_key(
    gateway_workspace: GatewayWorkspace, bare_client: httpx.AsyncClient
) -> None:
    token = await create_token(gateway_workspace.owner, scope="write")
    path = (
        f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys/"
        f"{gateway_workspace.gateway_key_id}"
    )
    response = await bare_client.patch(path, headers=bearer(token["token"]), json={"rpm_limit": 10})
    assert response.status_code == 200, response.text
    assert response.json()["rpm_limit"] == 10
    saved = await gateway_workspace.owner.get(
        f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys"
    )
    assert saved.status_code == 200
    assert saved.json()[0]["rpm_limit"] == 10
