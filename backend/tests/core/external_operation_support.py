"""A real HTTP model provider and an API workspace for receipt fault regressions."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.db.rls import bind_project
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.main import create_app
from tests.helpers import create_workspace, span


@dataclass
class HttpModelProvider:
    url: str = ""
    output: str = "hello"
    disconnect: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    requests: list[dict] = field(default_factory=list)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            self.requests.append(json.loads(await reader.readexactly(length)))
            self.entered.set()
            await self.gate.wait()
            if self.disconnect:
                return
            body = json.dumps(
                {
                    "id": "local-real-http",
                    "object": "chat.completion",
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": self.output},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 19, "completion_tokens": 9, "total_tokens": 28},
                }
            ).encode()
            writer.write(
                (
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
        except ConnectionError:
            pass  # Cancellation deliberately closes the client socket before its answer.
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.fixture
async def http_model_provider():
    provider = HttpModelProvider()
    server = await asyncio.start_server(provider.serve, "127.0.0.1", 0)
    provider.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1"
    try:
        yield provider
    finally:
        provider.gate.set()
        server.close()
        await server.wait_closed()


@pytest.fixture
async def real_provider_app(gateway_settings):
    settings = gateway_settings.model_copy(update={"gateway_allow_insecure_base_urls": True})
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def provider_workspace(browser_factory, http_model_provider, session_factory):
    owner = await browser_factory("real-http@example.com")
    workspace = await create_workspace(owner)
    base = f"/api/v1/orgs/{workspace.org_id}"
    credential = await owner.post(
        f"{base}/credentials",
        json={
            "name": "local",
            "provider": "openai_compatible",
            "base_url": http_model_provider.url,
            "api_key": "local-test-only",
        },
    )
    assert credential.status_code == 201, credential.text
    priced = await owner.post(
        f"{base}/price-overrides",
        json={
            "provider": "openai_compatible",
            "model_pattern": "gpt-4.1-mini",
            "input_per_mtok": "1",
            "output_per_mtok": "2",
        },
    )
    assert priced.status_code == 201, priced.text
    project_id = uuid.UUID(workspace.project_id)
    source = span(
        trace_id="3" * 32,
        span_id="4" * 16,
        model="gpt-4.1-mini",
        input={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]},
    )
    async with session_factory() as db:
        await bind_project(db, project_id)
        outcome = await ingest_spans(
            db,
            IngestTarget(
                project_id=project_id, org_id=uuid.UUID(workspace.org_id), capture_payloads=True
            ),
            [source],
        )
        assert outcome.accepted == 1
        await db.commit()
    return SimpleNamespace(
        owner=owner,
        project_id=project_id,
        credential_id=credential.json()["id"],
        provider=http_model_provider,
        base=f"/api/v1/projects/{project_id}/playground",
    )
