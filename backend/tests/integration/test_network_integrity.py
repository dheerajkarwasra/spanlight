"""Public contracts through real provider/SDK HTTP and invocation-owned PostgreSQL 17."""

import asyncio
import json
import os
import secrets
from contextlib import AsyncExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.budgets.support import CHAT_BODY, PRICED_MODEL, chat_completion
from tests.gateway_support import drain_recorder
from tests.helpers import Browser, create_api_key, create_workspace
from tests.integration.network_support import (
    CommittedWriteFaultProxy,
    body_from,
    communicate_sdk,
    respond,
    running_application,
    serve_http,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def app(settings: Settings) -> Any:
    application = create_app(settings.model_copy(update={"sentry_dsn": None}))
    async with running_application(application):
        yield application


async def created(owner: Browser, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await owner.post(path, json=body)
    assert response.status_code == 201
    result: dict[str, Any] = response.json()
    return result


@pytest.mark.parametrize("edited_scope", [False, True])
async def test_budget_history_over_provider_socket(
    gateway_settings: Settings,
    edited_scope: bool,
) -> None:
    calls: list[dict[str, Any]] = []

    async def provider(scope: Any, receive: Any, send: Any) -> None:
        assert scope["path"] == "/v1/chat/completions"
        calls.append(json.loads(await body_from(receive)))
        completion = chat_completion(0)
        tokens = 800_000 if len(calls) == 1 else 100_000
        completion["usage"] = {
            "prompt_tokens": 0,
            "completion_tokens": tokens,
            "total_tokens": tokens,
        }
        await respond(send, json.dumps(completion).encode())

    request_body = {**CHAT_BODY, "max_tokens": 100_000}
    settings = gateway_settings.model_copy(update={"gateway_allow_insecure_base_urls": True})
    async with AsyncExitStack() as stack:
        upstream = await stack.enter_async_context(serve_http(provider))
        applications = [create_app(settings), create_app(settings)]
        for application in applications:
            await stack.enter_async_context(running_application(application))
        control = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=applications[0]),
                base_url="http://testserver",
                headers={"Origin": "http://testserver"},
            )
        )
        signup = await control.post(
            "/api/v1/auth/signup",
            json={
                "email": "network@example.com",
                "password": "correct horse battery",
                "name": "Owner",
            },
        )
        assert signup.status_code == 201
        owner = Browser(control, signup.json())
        workspace = await create_workspace(owner)
        credential = await created(
            owner,
            f"/api/v1/orgs/{workspace.org_id}/credentials",
            {
                "name": "loopback",
                "provider": "openai_compatible",
                "api_key": secrets.token_hex(24),
                "base_url": upstream + "/v1",
                "password": "correct horse battery",
            },
        )
        await created(
            owner,
            f"/api/v1/orgs/{workspace.org_id}/price-overrides",
            {
                "provider": "openai_compatible",
                "model_pattern": PRICED_MODEL,
                "input_per_mtok": "0",
                "output_per_mtok": "1",
                "cached_input_per_mtok": "0",
            },
        )
        prefix = f"/api/v1/projects/{workspace.project_id}"
        await created(
            owner,
            prefix + "/gateway/routes",
            {
                "name": "loopback",
                "config": {
                    "targets": [{"credential_id": credential["id"]}],
                    "retry": {},
                    "fallback": {},
                },
            },
        )
        key = await created(
            owner,
            prefix + "/gateway/keys",
            {"name": "network", "environment": "test", "password": "correct horse battery"},
        )
        clients = [
            await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application),
                    base_url="http://testserver",
                    headers={"Authorization": f"Bearer {key['secret']}"},
                )
            )
            for application in applications
        ]
        assert (
            await clients[0].post("/gw/v1/chat/completions", json=request_body)
        ).status_code == 200
        await drain_recorder()
        history = await owner.get(prefix + "/traces")
        assert history.status_code == 200
        assert Decimal(history.json()["items"][0]["cost_usd"]) == Decimal("0.80")
        budget = await created(
            owner,
            prefix + "/budgets",
            {
                "name": "daily",
                "scope": "model" if edited_scope else "project",
                "scope_id": "unrelated-model" if edited_scope else None,
                "period": "daily",
                "amount_usd": "1",
                "action": "block",
                "channel_ids": [],
            },
        )
        if edited_scope:
            edit = await owner.patch(
                prefix + f"/budgets/{budget['id']}",
                json={"scope": "model", "scope_id": PRICED_MODEL},
            )
            assert edit.status_code == 200
        assert (
            await clients[1].post("/gw/v1/chat/completions", json=request_body)
        ).status_code == 200
        await drain_recorder()
        assert (
            await clients[0].post("/gw/v1/chat/completions", json=request_body)
        ).status_code == 200
        await drain_recorder()
        refused = await clients[1].post("/gw/v1/chat/completions", json=request_body)
        assert refused.status_code == 402
        assert "$0.10 for this call" in refused.json()["error"]["message"]
        assert len(calls) == 3


@pytest.mark.parametrize("sdk", ["python", "typescript"])
async def test_committed_write_ambiguous_ack_replays_once(
    app: Any,
    browser_factory: Any,
    sdk: str,
) -> None:
    owner = await browser_factory("sdk-owner@example.com")
    workspace = await create_workspace(owner)
    stranger = await browser_factory("sdk-stranger@example.com")
    other = await create_workspace(stranger)
    key = await create_api_key(owner, workspace.project_id, scopes=["ingest:write", "scores:write"])
    commits: list[str] = []

    async def observed_commit(path: str, body: bytes) -> None:
        encoded = json.loads(body)
        collection = encoded["spans"] if path == "/v1/traces" else encoded["scores"]
        trace_id = collection[0]["trace_id"]
        url = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"
        if path == "/v1/traces":
            shown = await owner.get(url)
            assert shown.status_code == 200
            assert shown.json()["span_count"] == 3
            assert shown.json()["input_tokens"] == 500_000
            assert Decimal(shown.json()["cost_usd"]) == Decimal("0.20")
        else:
            shown = await owner.get(url + "/scores")
            assert shown.status_code == 200
            assert len(shown.json()) == 1
        commits.append(path)

    async with serve_http(app) as backend_url:
        proxy = CommittedWriteFaultProxy(backend_url, observed_commit)
        async with serve_http(proxy) as proxy_url:
            if sdk == "python":
                command = [
                    str(ROOT / "sdks/python/.venv/bin/python"),
                    str(FIXTURES / "sdk_python.py"),
                ]
            else:
                command = ["node", str(FIXTURES / "sdk_typescript.mjs")]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "SPANLIGHT_ENABLED": "true"},
            )
            payload = json.dumps(
                {
                    "api_key": key["secret"],
                    "host": proxy_url,
                    "sdk_path": str(ROOT / "sdks/typescript/dist/index.js"),
                }
            ).encode()
            stdout, _stderr = await communicate_sdk(process, payload)
            assert process.returncode == 0, "SDK subprocess failed (output withheld)"
            result = json.loads(stdout)
    assert (result["sent"], result["failed"], result["dropped"]) == (
        4 if sdk == "python" else 3,
        0,
        0,
    )
    assert commits == (["/v1/traces", "/v1/scores"] if sdk == "python" else ["/v1/traces"])
    attempts = proxy.requests["/v1/traces"]
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert proxy.acknowledgments["/v1/traces"] == [
        {"accepted": 3, "rejected": []},
        {"accepted": 3, "rejected": []},
    ]
    trace_id = result["trace_id"]
    prefix = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"
    shown = await owner.get(prefix)
    assert shown.status_code == 200
    trace = shown.json()
    assert trace["span_count"] == 3
    assert len(trace["spans"]) == 3
    assert len({item["span_id"] for item in trace["spans"]}) == 3
    assert (trace["input_tokens"], trace["output_tokens"]) == (500_000, 0)
    assert Decimal(trace["cost_usd"]) == Decimal("0.20")
    assert (await stranger.get(prefix)).status_code == 404
    empty = await stranger.get(f"/api/v1/projects/{other.project_id}/traces/{trace_id}")
    assert empty.status_code == 404
    if sdk == "python":
        scores = proxy.requests["/v1/scores"]
        assert len(scores) == 2
        assert scores[0] == scores[1]
        assert proxy.acknowledgments["/v1/scores"] == [
            {"accepted": 1, "rejected": []},
            {"accepted": 1, "rejected": []},
        ]
        stored = await owner.get(prefix + "/scores")
        assert stored.status_code == 200
        assert len(stored.json()) == 1
        assert stored.json()[0]["name"] == "quality"

    # Identical natural keys belong independently to the project selected by its API key.
    async with serve_http(app) as backend_url, httpx.AsyncClient(trust_env=False) as client:
        replay = await client.post(
            backend_url + "/v1/traces",
            content=attempts[0],
            headers={
                "Authorization": f"Bearer {other.api_key}",
                "Content-Type": "application/json",
            },
        )
    assert replay.status_code == 200
    assert replay.json() == {"accepted": 3, "rejected": []}
    isolated = await stranger.get(f"/api/v1/projects/{other.project_id}/traces/{trace_id}")
    assert isolated.status_code == 200
    assert isolated.json()["span_count"] == 3
    assert isolated.json()["input_tokens"] == 500_000
    assert Decimal(isolated.json()["cost_usd"]) == Decimal("0.20")
    assert (await stranger.get(prefix)).status_code == 404
    original = await owner.get(prefix)
    assert original.json()["span_count"] == 3
    assert Decimal(original.json()["cost_usd"]) == Decimal("0.20")
