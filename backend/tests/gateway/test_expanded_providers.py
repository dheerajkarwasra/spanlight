"""Expanded provider calls through the keyed gateway and Postgres recorder."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import ProviderKind
from app.gateway.schemas import CredentialCreate
from app.gateway.service import create_credential
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    public_resolver,
    read_gateway_spans,
)

AZURE = "https://fixture-resource.openai.azure.com/openai/v1"
DEPLOYMENT = "gpt-4o"
CHAT = {"model": "ask", "messages": [{"role": "user", "content": "Hello"}]}


async def _key(
    workspace: GatewayWorkspace, credential_id: str, *, deployment: str | None = None
) -> dict[str, str]:
    prefix = f"/api/v1/projects/{workspace.project_id}/gateway"
    target: dict[str, Any] = {"credential_id": credential_id}
    if deployment is not None:
        target["model_aliases"] = {"ask": deployment}
    route = await workspace.owner.post(
        f"{prefix}/routes",
        json={"name": "provider", "config": {"targets": [target], "retry": {}, "fallback": {}}},
    )
    assert route.status_code == 201, route.text
    key = await workspace.owner.post(
        f"{prefix}/keys",
        json={
            "name": "provider",
            "environment": "test",
            "password": "correct horse battery",
            "route_id": route.json()["id"],
        },
    )
    assert key.status_code == 201, key.text
    return {"Authorization": f"Bearer {key.json()['secret']}"}


@pytest.mark.parametrize("limit", ["project", "run"])
async def test_azure_override_prices_the_deployment_across_admission_and_settlement(
    limit: str,
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace = gateway_workspace
    async with session_factory() as db, db.begin():
        credential = await create_credential(
            db,
            workspace.org_id,
            UUID(workspace.owner.user["id"]),
            CredentialCreate(
                name="azure",
                provider=ProviderKind.AZURE_OPENAI,
                base_url=AZURE,
                api_key="azure-fixture",
            ),
            settings=gateway_settings,
            resolver=public_resolver,
        )
        credential_id = str(credential.id)
    headers = await _key(workspace, credential_id, deployment=DEPLOYMENT)
    path = "/gw/v1/chat/completions"
    answer = {
        "model": "gpt-4o-2024-08-06",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"content": "Hi"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    fake_provider.reply_json(answer)
    plain = await gateway_client.post(path, headers=headers, json=CHAT)
    assert plain.json() == answer

    if limit == "project":
        budget = await workspace.owner.post(
            f"/api/v1/projects/{workspace.project_id}/budgets",
            json={
                "name": "Spend",
                "scope": "project",
                "period": "daily",
                "amount_usd": "1",
                "action": "block",
                "channel_ids": [],
            },
        )
        assert budget.status_code == 201, budget.text
    else:
        headers["x-spanlight-run-budget-usd"] = "1"
    blocked = await gateway_client.post(path, headers=headers, json=CHAT)
    assert blocked.status_code == 402, blocked.text
    assert blocked.headers["x-spanlight-attempts"] == "0"
    assert len(fake_provider.requests) == 1

    override = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": "azure_openai",
            "model_pattern": DEPLOYMENT,
            "input_per_mtok": "0.25",
            "output_per_mtok": "0.50",
        },
    )
    assert override.status_code == 201, override.text
    fake_provider.reply_json(answer)
    accepted = await gateway_client.post(path, headers=headers, json=CHAT)
    assert accepted.status_code == 200, accepted.text
    assert accepted.headers["x-spanlight-attempts"] == "1"
    assert len(fake_provider.requests) == 2
    assert all(request.url == AZURE + "/chat/completions" for request in fake_provider.requests)
    assert all(request.json["model"] == DEPLOYMENT for request in fake_provider.requests)
    assert all(request.headers["api-key"] == "azure-fixture" for request in fake_provider.requests)

    before, refused, settled = await read_gateway_spans(session_factory, workspace.project_id)
    assert before["model"] == settled["model"] == DEPLOYMENT
    assert before["provider"] == settled["provider"] == "azure_openai"
    assert before["cost_usd"] is None
    assert refused["attributes"]["spanlight.budget.blocked"] is True
    assert settled["cost_usd"] == Decimal("0.00003000")
    assert settled["pricing_version"] == f"override:{override.json()['id']}"


@pytest.mark.parametrize("hosted", [False, True])
async def test_xai_responses_sse_keeps_usage_and_only_prices_supported_dimensions(
    hosted: bool,
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace = gateway_workspace
    saved = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"name": "xAI", "provider": "xai", "api_key": "xai-fixture"},
    )
    assert saved.status_code == 201, saved.text
    headers = await _key(workspace, saved.json()["id"])
    discovery = {"object": "list", "data": [{"id": "grok-4.7"}]}
    fake_provider.reply_json(discovery)
    listed = await gateway_client.get("/gw/v1/models", headers=headers)
    assert listed.json() == discovery

    payload: dict[str, Any] = {"model": "grok-4.7", "input": "Hello", "stream": True}
    if hosted:
        payload["tools"] = [{"type": "web_search"}]
        blocked = await gateway_client.post(
            "/gw/v1/responses",
            headers={**headers, "x-spanlight-run-budget-usd": "50"},
            json=payload,
        )
        assert blocked.status_code == 402, blocked.text
        assert len(fake_provider.requests) == 1

    terminal = {
        "type": "response.incomplete",
        "response": {
            "id": "xai-fixture-response",
            "status": "incomplete",
            "model": "grok-4.7",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "input_tokens_details": {"cached_tokens": 50},
                "output_tokens_details": {"reasoning_tokens": 4},
            },
        },
    }
    frames = [f"event: response.incomplete\ndata: {json.dumps(terminal)}\n\n".encode()]
    fake_provider.reply_stream(frames)
    streamed = await gateway_client.post("/gw/v1/responses", headers=headers, json=payload)
    assert streamed.status_code == 200, streamed.text
    assert streamed.content == b"".join(frames)
    models, response = fake_provider.requests
    assert models.url == "https://api.x.ai/v1/models"
    assert response.url == "https://api.x.ai/v1/responses"
    assert response.json == payload

    spans = await read_gateway_spans(session_factory, workspace.project_id)
    span = spans[-1]
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (100, 10, 50)
    assert span["finish_reason"] == "length"
    assert span["cost_usd"] == (None if hosted else Decimal("0.00018500"))


async def test_together_discovery_wraps_a_native_list_for_openai_clients(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
) -> None:
    workspace = gateway_workspace
    saved = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"name": "Together", "provider": "together", "api_key": "together-fixture"},
    )
    assert saved.status_code == 201, saved.text
    headers = await _key(workspace, saved.json()["id"])
    upstream = [{"id": "Qwen/Qwen3.5-9B", "object": "model", "created": 1, "owned_by": "Qwen"}]
    fake_provider.reply_json(upstream)

    listed = await gateway_client.get("/gw/v1/models", headers=headers)
    assert listed.json() == {"object": "list", "data": upstream}
    [sent] = fake_provider.requests
    assert sent.url == "https://api.together.ai/v1/models"


async def test_fixed_host_check_stores_failure_without_echoing_provider_body(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    owner = gateway_workspace.owner
    credentials = f"/api/v1/orgs/{gateway_workspace.org_id}/credentials"
    key = "mistral-test-key"
    added = await owner.post(
        credentials, json={"name": "Mistral", "provider": "mistral", "api_key": key}
    )
    assert added.status_code == 201, added.text
    fake_provider.reply_json({"message": key}, status=401)

    checked = await owner.post(f"{credentials}/{added.json()['id']}/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "error"
    assert checked.json()["error"] == "401 Unauthorized"
    assert key not in checked.text
    assert [request.url for request in fake_provider.requests] == [
        "https://api.mistral.ai/v1/models"
    ]
    listed = await owner.get(credentials)
    assert key not in listed.text
    [mistral] = [item for item in listed.json() if item["provider"] == "mistral"]
    assert mistral["last_error"] == "401 Unauthorized"
