"""Real local streams and API provisioning for HTTP admission tests."""

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.main import create_app, create_gateway_app
from tests.helpers import Browser, Workspace, create_workspace
from tests.integration.network_support import body_from, respond, running_application, serve_http


class HeldProvider:
    def __init__(self) -> None:
        self.active = 0
        self.calls = 0
        self.broken = False
        self.release = asyncio.Event()
        self.changed = asyncio.Event()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["method"] == "GET":
            await respond(send, b'{"object":"list","data":[]}')
            return
        await body_from(receive)
        self.active += 1
        self.changed.set()
        self.calls += 1
        disconnected = asyncio.create_task(receive())
        released = asyncio.create_task(self.release.wait())
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "more_body": True,
                    "body": b'data: {"id":"local","choices":'
                    b'[{"index":0,"delta":{"content":"hello"}}]}\n\n',
                }
            )
            await asyncio.wait((disconnected, released), return_when=asyncio.FIRST_COMPLETED)
            if self.release.is_set():
                if self.broken:
                    raise OSError("local provider cut stream")
                await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})
        finally:
            self.active -= 1
            self.changed.set()
            for task in (disconnected, released):
                task.cancel()
            await asyncio.gather(disconnected, released, return_exceptions=True)


async def created(owner: Browser, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await owner.post(path, json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


@dataclass
class AdmissionWorld:
    application: Any
    provider: HeldProvider
    client: httpx.AsyncClient
    owner: Browser
    workspace: Workspace
    base: str
    key: str
    key_id: str
    api_release: asyncio.Event
    api_entered: asyncio.Event


async def world(
    stack: AsyncExitStack, settings: Settings, standalone: bool, cap: int = 2
) -> AdmissionWorld:
    provider = HeldProvider()
    upstream = await stack.enter_async_context(serve_http(provider))
    configured = settings.model_copy(
        update={
            "gateway_allow_insecure_base_urls": True,
            "gateway_max_in_flight": cap,
            "api_max_in_flight": 1,
            "worker_required": False,
        }
    )
    control = create_app(configured)
    await stack.enter_async_context(running_application(control))
    http = await stack.enter_async_context(
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=control),
            base_url="http://testserver",
            headers={"Origin": "http://testserver"},
        )
    )
    signup = await http.post(
        "/api/v1/auth/signup",
        json={
            "email": "admission@example.com",
            "password": "correct horse battery",
            "name": "Owner",
        },
    )
    assert signup.status_code == 201
    owner = Browser(http, signup.json())
    workspace = await create_workspace(owner)
    credential = await created(
        owner,
        f"/api/v1/orgs/{workspace.org_id}/credentials",
        {
            "name": "local",
            "provider": "openai_compatible",
            "api_key": "local-synthetic",
            "base_url": upstream + "/v1",
            "password": "correct horse battery",
        },
    )
    prefix = f"/api/v1/projects/{workspace.project_id}"
    await created(
        owner,
        prefix + "/gateway/routes",
        {
            "name": "local",
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
        {"name": "local", "environment": "test", "password": "correct horse battery"},
    )
    application = control
    if standalone:
        application = create_gateway_app(configured)
        await stack.enter_async_context(running_application(application))
    api_release, api_entered = asyncio.Event(), asyncio.Event()

    async def held_api() -> dict[str, bool]:
        api_entered.set()
        await api_release.wait()
        return {"ok": True}

    application.add_api_route("/api/admission-test", held_api, methods=["GET"])
    base = await stack.enter_async_context(serve_http(application))
    client = await stack.enter_async_context(
        httpx.AsyncClient(
            base_url=base,
            timeout=5,
            trust_env=False,
            cookies=http.cookies,
            headers={"Authorization": f"Bearer {key['secret']}"},
        )
    )
    return AdmissionWorld(
        application,
        provider,
        client,
        owner,
        workspace,
        base,
        key["secret"],
        key["id"],
        api_release,
        api_entered,
    )


async def unfinished_request(
    base: str, path: str, key: str, *, anthropic: bool = False
) -> tuple[int, dict[str, str], Any]:
    url = urlsplit(base)
    reader, writer = await asyncio.open_connection(url.hostname, url.port)
    try:
        extra = "Anthropic-Version: 2023-06-01\r\n" if anthropic else ""
        writer.write(
            (
                f"POST {path} HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer {key}\r\n"
                "Content-Type: application/json\r\nContent-Length: 9999999\r\n"
                f"X-Request-ID: admission-overload\r\n{extra}\r\n"
            ).encode()
        )
        await writer.drain()
        async with asyncio.timeout(1):
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            headers = {
                name.lower(): value
                for name, value in (line.split(": ", 1) for line in lines[1:] if ": " in line)
            }
            body = await reader.readexactly(int(headers["content-length"]))
        return int(lines[0].split()[1]), headers, json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def wait_active(provider: HeldProvider, count: int) -> None:
    async with asyncio.timeout(5):
        while provider.active != count:
            provider.changed.clear()
            await provider.changed.wait()


CHAT = {"model": "gpt-4.1-mini", "stream": True, "messages": [{"role": "user", "content": "local"}]}
