"""Native Gemini REST and SSE through a real Spanlight gateway key and Postgres."""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.main import create_gateway_app
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

MODEL = "gemini-3.5-flash-lite"
ALIAS = "ask"
PATH = f"/gw/v1beta/models/{ALIAS}:generateContent"
STREAM = f"/gw/v1beta/models/{ALIAS}:streamGenerateContent"
GOOGLE_KEY = "google-fixture-key"
BODY = {"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}
ANSWER = {
    "modelVersion": MODEL,
    "responseId": "gemini-test",
    "candidates": [
        {
            "index": 0,
            "content": {"role": "model", "parts": [{"text": "Hi"}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 20,
        "cachedContentTokenCount": 3,
        "candidatesTokenCount": 2,
        "thoughtsTokenCount": 4,
    },
}


@pytest.fixture
async def google_headers(gateway_workspace: GatewayWorkspace) -> dict[str, str]:
    return await _google_headers(gateway_workspace, restricted=True)


async def _google_headers(workspace: GatewayWorkspace, *, restricted: bool) -> dict[str, str]:
    credential = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        json={"name": "gemini", "provider": "gemini", "api_key": GOOGLE_KEY},
    )
    assert credential.status_code == 201, credential.text
    url = f"/api/v1/projects/{workspace.project_id}/gateway"
    route = await workspace.owner.post(
        f"{url}/routes",
        json={
            "name": "native gemini",
            "config": {
                "targets": [
                    {
                        "credential_id": credential.json()["id"],
                        "model_aliases": {ALIAS: MODEL, "other": MODEL} if restricted else {},
                    }
                ],
                "retry": {},
                "fallback": {},
            },
        },
    )
    assert route.status_code == 201, route.text
    key = await workspace.owner.post(
        f"{url}/keys",
        json={
            "name": "Gemini native",
            "environment": "test",
            "password": "correct horse battery",
            "route_id": route.json()["id"],
            "allowed_models": [ALIAS] if restricted else [],
        },
    )
    assert key.status_code == 201, key.text
    return {"x-goog-api-key": key.json()["secret"]}


@pytest.mark.parametrize("standalone", [False, True])
async def test_native_generation(
    standalone: bool,
    gateway_client: httpx.AsyncClient,
    gateway_settings: Settings,
    gateway_workspace: GatewayWorkspace,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(ANSWER)
    if standalone:
        app = create_gateway_app(gateway_settings, gateway_transport=fake_provider)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            response = await client.post(PATH, headers=google_headers, json=BODY)
    else:
        response = await gateway_client.post(PATH, headers=google_headers, json=BODY)

    assert response.status_code == 200, response.text
    assert response.json() == ANSWER
    [sent] = fake_provider.requests
    assert (
        sent.url
        == f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    )
    assert sent.headers["x-goog-api-key"] == GOOGLE_KEY
    assert "authorization" not in sent.headers
    assert sent.json == BODY
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["provider"], span["model"]) == ("google", MODEL)
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (20, 6, 3)
    assert span["cost_usd"] is not None
    assert span["attributes"]["provider.response_id"] == "gemini-test"


async def test_native_model_visibility_and_google_errors(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    unauthorized = await gateway_client.post(PATH, json=BODY)
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["status"] == "UNAUTHENTICATED"
    conflict = await gateway_client.post(
        PATH, headers={**google_headers, "Authorization": "Bearer conflicting"}, json=BODY
    )
    assert conflict.status_code == 401
    bad = await gateway_client.post(PATH, headers=google_headers, json={**BODY, "model": "other"})
    assert bad.status_code == 400
    assert bad.json()["error"]["status"] == "INVALID_ARGUMENT"
    forbidden = await gateway_client.post(
        "/gw/v1beta/models/other:generateContent", headers=google_headers, json=BODY
    )
    assert forbidden.status_code == 404
    listed = await gateway_client.get("/gw/v1beta/models", headers=google_headers)
    assert listed.status_code == 200, listed.text
    assert [item["name"] for item in listed.json()["models"]] == ["models/ask"]
    assert (await gateway_client.get(PATH, headers=google_headers)).json()["error"]["status"] == (
        "UNIMPLEMENTED"
    )
    assert not fake_provider.requests


async def test_native_discovery_proxies_pagination_and_strips_other_query_fields(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
) -> None:
    headers = await _google_headers(gateway_workspace, restricted=False)
    page = {"models": [{"name": f"models/{MODEL}"}], "nextPageToken": "more"}
    fake_provider.reply_json(page)

    response = await gateway_client.get(
        "/gw/v1beta/models",
        headers=headers,
        params={"pageSize": 5, "pageToken": "opaque &=", "ignored": "value"},
    )

    assert response.json() == page
    [sent] = fake_provider.requests
    url = httpx.URL(sent.url)
    assert url.path == "/v1beta/models"
    assert dict(url.params) == {"pageSize": "5", "pageToken": "opaque &="}
    assert sent.headers["x-goog-api-key"] == GOOGLE_KEY


def _frame(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


@pytest.mark.parametrize("complete", [True, False])
async def test_native_stream_usage_requires_finish_and_final_counts(
    complete: bool,
    gateway_client: httpx.AsyncClient,
    gateway_workspace: GatewayWorkspace,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chunk = {**ANSWER, "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 2}}
    if complete:
        del chunk["usageMetadata"]
        frames = [_frame(chunk), _frame({"usageMetadata": ANSWER["usageMetadata"]})]
    else:
        chunk["candidates"] = [{"index": 0, "content": {"parts": [{"text": "Hi"}]}}]
        frames = [_frame(chunk)]
    fake_provider.reply_stream(frames)
    response = await gateway_client.post(STREAM, headers=google_headers, json=BODY)

    assert response.status_code == 200, response.text
    assert response.content == b"".join(frames)
    [sent] = fake_provider.requests
    assert sent.url.endswith(f"/{MODEL}:streamGenerateContent?alt=sse")
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["output_tokens"] == (6 if complete else None)
    assert span["attributes"]["provider.response_id"] == "gemini-test"
    if not complete:
        assert span["cost_usd"] is None
        assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"


async def test_native_run_budget_checks_all_candidates_before_dispatch(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    headers = {**google_headers, "x-spanlight-run-budget-usd": "0.65"}
    config = {"maxOutputTokens": 25000}
    response = await gateway_client.post(
        PATH,
        headers=headers,
        json={**BODY, "generationConfig": {**config, "candidateCount": 2}},
    )

    assert response.status_code == 402, response.text
    assert response.headers["x-spanlight-code"] == "BUDGET_EXCEEDED"
    assert not fake_provider.requests

    fake_provider.reply_json(ANSWER)
    accepted = await gateway_client.post(
        PATH, headers=headers, json={**BODY, "generationConfig": config}
    )
    assert accepted.status_code == 200, accepted.text


async def test_native_run_budget_clamps_default_and_refuses_unbounded_inputs(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    headers = {**google_headers, "x-spanlight-run-budget-usd": "10"}
    blocked = await gateway_client.post(
        PATH,
        headers=headers,
        json={**BODY, "tools": [{"googleSearch": {}}]},
    )
    assert blocked.status_code == 402, blocked.text
    assert not fake_provider.requests

    fake_provider.reply_json(ANSWER)
    result = await gateway_client.post(PATH, headers=headers, json=BODY)
    assert result.status_code == 200, result.text
    [request] = fake_provider.requests
    assert request.json == {**BODY, "generationConfig": {"maxOutputTokens": 16384}}


async def test_native_project_budget_refuses_the_same_two_candidate_request(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    budget = await gateway_workspace.owner.post(
        f"/api/v1/projects/{gateway_workspace.project_id}/budgets",
        json={
            "name": "Gemini spend",
            "scope": "project",
            "period": "daily",
            "amount_usd": "0.65",
            "action": "block",
            "channel_ids": [],
        },
    )
    assert budget.status_code == 201, budget.text

    result = await gateway_client.post(
        PATH,
        headers=google_headers,
        json={**BODY, "generationConfig": {"maxOutputTokens": 25000, "candidateCount": 2}},
    )

    assert result.status_code == 402, result.text
    assert result.json()["error"]["status"] == "PERMISSION_DENIED"
    assert not fake_provider.requests


async def test_native_protected_failure_does_not_retry_a_billable_attempt(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    unavailable = {"error": {"code": 503, "status": "UNAVAILABLE", "message": "capacity"}}
    fake_provider.reply_json(unavailable, status=503)
    fake_provider.reply_json(ANSWER)

    result = await gateway_client.post(
        PATH,
        headers={**google_headers, "x-spanlight-run-budget-usd": "10"},
        json=BODY,
    )

    assert result.status_code == 503, result.text
    assert result.json() == unavailable
    assert len(fake_provider.requests) == 1


async def test_native_grounding_keeps_reported_tokens_but_leaves_total_unpriced(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
    gateway_workspace: GatewayWorkspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(ANSWER)
    result = await gateway_client.post(
        PATH,
        headers=google_headers,
        json={**BODY, "tools": [{"googleSearch": {}}]},
    )

    assert result.status_code == 200, result.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["input_tokens"], span["output_tokens"]) == (20, 6)
    assert span["cost_usd"] is None
    assert span["attributes"]["usage.unpriced_reason"] == "gemini_billing_dimensions"


async def test_native_prompt_block_can_report_input_only_usage(
    gateway_client: httpx.AsyncClient,
    google_headers: dict[str, str],
    fake_provider: FakeProvider,
    gateway_workspace: GatewayWorkspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(
        {
            "promptFeedback": {"blockReason": "SAFETY"},
            "usageMetadata": {"promptTokenCount": 20},
        }
    )
    result = await gateway_client.post(PATH, headers=google_headers, json=BODY)

    assert result.json()["promptFeedback"]["blockReason"] == "SAFETY"
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["input_tokens"], span["output_tokens"]) == (20, 0)
    assert span["attributes"]["finish_reason"] == "SAFETY"
