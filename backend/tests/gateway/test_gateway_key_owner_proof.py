"""A gateway key spends the organization's provider credentials: creating or widening one needs
the account owner, not just a session."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import GatewayKey
from app.db.rls import bypass_rls
from tests.gateway_fixtures import GatewayWorkspace


async def test_session_without_proof_cannot_create_gateway_key(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    keys = f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys"

    response = await gateway_workspace.owner.post(
        keys, json={"name": "stolen", "environment": "prod"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PASSWORD"
    async with session_factory() as db:
        await bypass_rls(db)
        names = (await db.scalars(select(GatewayKey.name))).all()
    assert "stolen" not in names


async def test_session_without_proof_cannot_widen_gateway_key(
    gateway_workspace: GatewayWorkspace,
) -> None:
    keys = f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys"
    key = f"{keys}/{gateway_workspace.gateway_key_id}"
    keep = await gateway_workspace.owner.patch(key, json={"rpm_limit": 10})
    assert keep.status_code == 200, keep.text  # tightening needs no proof

    widen = await gateway_workspace.owner.patch(key, json={"rpm_limit": None})

    assert widen.status_code == 422, widen.text
    assert widen.json()["code"] == "INVALID_PASSWORD"
