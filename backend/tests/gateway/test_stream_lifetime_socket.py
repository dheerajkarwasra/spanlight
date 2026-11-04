"""Real TCP backpressure and header gating through the production gateway and adapters."""

import asyncio
import json
import socket
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Message, Receive, Scope, Send

from app.config import Settings
from app.db.rls import bypass_rls
from app.gateway import stream_forward, upstream_io
from app.gateway.adapters import UpstreamResponse, anthropic
from tests.gateway import admission_support
from tests.gateway.admission_support import CHAT, created, wait_active, world
from tests.gateway_support import drain_recorder
from tests.helpers import Browser
from tests.integration.network_support import body_from, respond, serve_http


class FloodProvider(admission_support.HeldProvider):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["method"] == "GET":
            await respond(send, b'{"object":"list","data":[]}')
            return
        await body_from(receive)
        self.active += 1
        self.calls += 1
        self.changed.set()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        gone = asyncio.ensure_future(receive())
        try:
            while not gone.done() and not self.release.is_set():
                await send(
                    {
                        "type": "http.response.body",
                        "more_body": True,
                        "body": b":" + b"x" * 65530 + b"\n\n",
                    }
                )
                await asyncio.sleep(0)
            if self.release.is_set():
                await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})
        finally:
            self.active -= 1
            self.changed.set()
            gone.cancel()
            await asyncio.gather(gone, return_exceptions=True)


@pytest.mark.parametrize("surface", ["chat/completions", "responses", "messages"])
@pytest.mark.parametrize("gate", ["headers", "body"])
async def test_response_lifetime_closes_blocked_send_and_recovers_capacity(
    gateway_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    surface: str,
) -> None:
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.8)
    monkeypatch.setattr(admission_support, "HeldProvider", FloodProvider)
    provider_kind = "anthropic" if surface == "messages" else "openai_compatible"
    original_created = admission_support.created

    async def surface_created(owner: Browser, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.endswith("/credentials"):
            body = {**body, "provider": provider_kind}
            if provider_kind == "anthropic":
                local_base = body.pop("base_url").removesuffix("/v1")
                monkeypatch.setattr(anthropic, "ANTHROPIC_API_BASE", local_base)
        return await original_created(owner, path, body)

    monkeypatch.setattr(admission_support, "created", surface_created)
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False, cap=1)
        if surface == "messages":
            w.client.headers["anthropic-version"] = "2023-06-01"
        await created(
            w.owner,
            f"/api/v1/orgs/{w.workspace.org_id}/price-overrides",
            {
                "provider": provider_kind,
                "model_pattern": "gpt-4.1-mini",
                "input_per_mtok": "1",
                "output_per_mtok": "1",
                "cached_input_per_mtok": "1",
            },
        )
        budget = await created(
            w.owner,
            f"/api/v1/projects/{w.workspace.project_id}/budgets",
            {
                "name": "local",
                "scope": "project",
                "period": "daily",
                "amount_usd": "1",
                "action": "block",
                "channel_ids": [],
            },
        )
        pending, done, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        completed = 0
        first_send: float | None = None

        async def application(scope: Scope, receive: Receive, send: Send) -> None:
            async def observed(message: Message) -> None:
                nonlocal completed, first_send
                if first_send is None:
                    first_send = asyncio.get_running_loop().time()
                if gate == "headers" and message["type"] == "http.response.start":
                    pending.set()
                    await release.wait()
                elif gate == "body" and message["type"] == "http.response.body":
                    pending.set()
                await send(message)
                completed += 1
                pending.clear()

            try:
                await w.application(scope, receive, observed)
            finally:
                done.set()

        base = await stack.enter_async_context(serve_http(application))
        url = urlsplit(base)
        reader, writer = await asyncio.open_connection(url.hostname, url.port, limit=1024)
        writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        payload = json.dumps({**CHAT, "max_tokens": 64}).encode()
        writer.write(
            (
                f"POST /gw/v1/{surface} HTTP/1.1\r\nHost: local\r\n"
                f"Authorization: Bearer {w.key}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        try:
            if gate == "body":
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                assert b"200 OK" in header
            async with asyncio.timeout(5):
                while True:
                    await pending.wait()
                    before = completed
                    await asyncio.sleep(0.08)
                    if pending.is_set() and completed == before:
                        break
            print(f"{gate}: pending send, plateau={completed}, provider_active={w.provider.active}")
            assert not done.is_set()
            await asyncio.wait_for(done.wait(), 2)
            assert first_send is not None
            elapsed = asyncio.get_running_loop().time() - first_send
            print(f"{surface}/{gate}: closed after {elapsed:.3f}s")
            await wait_active(w.provider, 0)
            await drain_recorder()
            prefix = f"/api/v1/projects/{w.workspace.project_id}"
            history = await w.owner.get(prefix + "/traces")
            [trace] = history.json()["items"]
            detail = await w.owner.get(prefix + "/traces/" + trace["trace_id"])
            [span] = detail.json()["spans"]
            assert span["status"] == "error"
            assert span["cost_usd"] is None
            assert "response send" in span["status_message"]
            async with session_factory() as db:
                await bypass_rls(db)
                rows = (
                    await db.execute(
                        text(
                            "SELECT status, amount_usd, charged_usd FROM budget_reservations "
                            "WHERE budget_id = :id"
                        ),
                        {"id": budget["id"]},
                    )
                ).all()
            [(status, amount, charged)] = rows
            assert status == "settled"
            assert amount > 0
            assert charged == amount
            assert (await w.client.get("/gw/v1/models")).status_code == 200
            w.provider.release.set()
            normal = await w.client.post(f"/gw/v1/{surface}", json={**CHAT, "max_tokens": 64})
            assert normal.status_code == 200
            assert normal.content == b"data: [DONE]\n\n"
        finally:
            release.set()
            w.provider.release.set()
            writer.close()
            await writer.wait_closed()


async def test_repeated_request_cancellation_keeps_capacity_until_cleanup(
    gateway_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False, cap=1)
        entered, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        request_tasks = []
        original = upstream_io.close_quietly

        async def held(response: UpstreamResponse | None) -> None:
            entered.set()
            await release.wait()
            await original(response)

        monkeypatch.setattr(stream_forward, "close_quietly", held)

        async def application(scope: Scope, receive: Receive, send: Send) -> None:
            current = asyncio.current_task()
            assert current is not None
            request_tasks.append(current)
            try:
                await w.application(scope, receive, send)
            finally:
                done.set()

        base = await stack.enter_async_context(serve_http(application))
        async with w.client.stream("POST", base + "/gw/v1/chat/completions", json=CHAT) as response:
            assert response.status_code == 200
            await anext(response.aiter_bytes())
        await asyncio.wait_for(entered.wait(), 2)
        try:
            [task] = request_tasks
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0.01)
                refused = await w.client.get("/gw/v1/models")
                assert refused.status_code == 503
                assert not done.is_set()
        finally:
            release.set()
            await asyncio.wait_for(done.wait(), 2)
        await wait_active(w.provider, 0)
        assert (await w.client.get("/gw/v1/models")).status_code == 200


@pytest.mark.parametrize("pause", [0.0, 0.03, 0.1])
async def test_client_resumes_before_deadline_and_finishes_normally(
    gateway_settings: Settings, monkeypatch: pytest.MonkeyPatch, pause: float
) -> None:
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.5)
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False, cap=1)
        async with w.client.stream("POST", "/gw/v1/chat/completions", json=CHAT) as response:
            assert response.status_code == 200
            iterator = response.aiter_bytes()
            first = await anext(iterator)
            assert b'"hello"' in first
            await asyncio.sleep(pause)
            w.provider.release.set()
            rest = b"".join([chunk async for chunk in iterator])
            assert rest == b"data: [DONE]\n\n"
        await wait_active(w.provider, 0)
        await drain_recorder()
        prefix = f"/api/v1/projects/{w.workspace.project_id}"
        history = await w.owner.get(prefix + "/traces")
        [trace] = history.json()["items"]
        detail = await w.owner.get(prefix + "/traces/" + trace["trace_id"])
        [span] = detail.json()["spans"]
        assert span["status"] == "ok"
        assert span["status_message"] is None
        assert (await w.client.get("/gw/v1/models")).status_code == 200


async def test_total_expiry_while_reading_upstream_is_not_a_response_send_timeout(
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.1)
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False, cap=1)
        with pytest.raises(httpx.RemoteProtocolError):
            await w.client.post("/gw/v1/chat/completions", json=CHAT)
        await wait_active(w.provider, 0)
        await drain_recorder()
        output = capsys.readouterr().out
        assert "gateway.stream_response_timeout" not in output
        assert output.count("gateway.stream_total_timeout") == 1
        assert "gateway.stream_upstream_idle_timeout" not in output
