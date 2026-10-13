"""`GatewayStreamingResponse` behind a real ASGI server's `send`: a client gone mid-stream.

Once the client has gone, the server's `send` raises. Starlette lets that error out through the
server (servers announcing ASGI 2.4 or later get `ClientDisconnect`, older ones the error
itself), so the response's own `finally` is what closes the provider stream and writes the span,
before the error reaches the server, not whenever the generator is collected.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect

from app.api.gw import GatewayStreamingResponse
from tests.asgi_harness import ClientGone, Message, OnMessage, call_asgi
from tests.gateway.support import CHAT_BODY, chat_frames
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

CALL_PATH = "/gw/v1/chat/completions"


@dataclass
class Watched:
    """Each `GatewayStreamingResponse` served, and the provider's open streams as each returned."""

    responses: list[GatewayStreamingResponse] = field(default_factory=list)
    open_when_done: list[int] = field(default_factory=list)


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch, fake_provider: FakeProvider) -> Watched:
    seen = Watched()
    serve = GatewayStreamingResponse.__call__

    async def watching(self: GatewayStreamingResponse, *args: Any) -> None:
        seen.responses.append(self)
        try:
            await serve(self, *args)
        finally:
            seen.open_when_done.append(fake_provider.open_streams)

    monkeypatch.setattr(GatewayStreamingResponse, "__call__", watching)
    return seen


def leave_after_the_first_chunk(bodies: list[bytes]) -> OnMessage:
    """A client that keeps the first body chunk in `bodies`, then is gone."""

    async def on_message(message: Message) -> None:
        if message["type"] == "http.response.body":
            if bodies:
                raise ClientGone("connection reset by peer")
            bodies.append(message["body"])

    return on_message


async def _stream_call(app: Any, key: str, bodies: list[bytes], spec_version: str) -> None:
    headers = [("authorization", f"Bearer {key}"), ("content-type", "application/json")]
    await call_asgi(
        app,
        CALL_PATH,
        headers=headers,
        body=json.dumps({**CHAT_BODY, "stream": True}).encode(),
        on_message=leave_after_the_first_chunk(bodies),
        spec_version=spec_version,
    )


@pytest.mark.parametrize(
    ("spec_version", "raised"), [("2.3", ClientGone), ("2.4", ClientDisconnect)]
)
async def test_streaming_response_closes_the_stream_when_send_fails(
    spec_version: str,
    raised: type[Exception],
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    watched: Watched,
) -> None:
    frames = chat_frames()
    fake_provider.reply_stream(frames)
    bodies: list[bytes] = []

    with pytest.raises(raised):
        await _stream_call(gateway_app, gateway_workspace.gateway_key, bodies, spec_version)

    assert bodies == [frames[0]]
    # The provider stream was closed before the response gave up, not later by the collector.
    assert watched.open_when_done == [0]
    assert (fake_provider.streams_opened, fake_provider.streams_closed) == (1, 1)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["attributes"]["spanlight.gateway.client_disconnected"] is True
    # Closing again does nothing: no second close upstream, no second span.
    calls = fake_provider.aclose_calls
    await watched.responses[0].body_iterator.aclose()  # type: ignore[attr-defined]
    assert fake_provider.aclose_calls == calls
    await read_gateway_span(session_factory, gateway_workspace.project_id)
