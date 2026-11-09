"""A credential with its own `base_url` decides where prompts go: adding one needs the account
owner, so a stolen session cannot send a route's traffic to its own host."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProviderCredential
from app.db.rls import bypass_rls
from tests.gateway_fixtures import GatewayWorkspace
from tests.helpers import create_token

ATTACKER = {
    "name": "stolen",
    "provider": "openai_compatible",
    "api_key": "sk-attacker",
    "base_url": "https://1.1.1.1/v1",
}


async def test_a_base_url_credential_needs_owner_proof(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = gateway_workspace.owner
    credentials = f"/api/v1/orgs/{gateway_workspace.org_id}/credentials"

    stolen = await owner.post(credentials, json=ATTACKER)

    assert stolen.status_code == 422, stolen.text
    assert stolen.json()["code"] == "INVALID_PASSWORD"
    token = (await create_token(owner))["token"]
    through_token = await owner.http.post(
        credentials, json=ATTACKER, headers={"Authorization": f"Bearer {token}"}
    )
    assert through_token.status_code == 403, through_token.text
    assert through_token.json()["code"] == "SESSION_REQUIRED"
    async with session_factory() as db:
        await bypass_rls(db)
        names = (await db.scalars(select(ProviderCredential.name))).all()
    assert "stolen" not in names

    plain = {"name": "own-openai", "provider": "openai", "api_key": "sk-own"}
    assert (await owner.post(credentials, json=plain)).status_code == 201


async def test_a_typo_in_the_base_url_does_not_ask_for_proof_first(
    gateway_workspace: GatewayWorkspace,
) -> None:
    owner = gateway_workspace.owner
    credentials = f"/api/v1/orgs/{gateway_workspace.org_id}/credentials"

    response = await owner.post(credentials, json={**ATTACKER, "base_url": "http://nope.example"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["field"] == "base_url"
