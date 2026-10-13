"""Helpers shared by the gateway streaming tests: request bodies, scripted frames, small reads."""

import dataclasses
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.context import ByteStream, GatewayContext, GatewayRequest
from tests.gateway_support import fixture_text, sse_frames

CHAT_BODY: dict[str, Any] = {
    "model": "gpt-4.1-mini",
    "messages": [{"role": "user", "content": "Hello"}],
}
# `chat_stream.sse` usage: 19 input tokens (8 of them cached) and 9 output tokens, at the seed
# gpt-4.1-mini rates (0.40 input, 0.10 cached input, 1.60 output per million tokens).
CHAT_COST = Decimal("0.00001960")
MESSAGES_BODY: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "max_tokens": 64,
    "stream": True,
    "messages": [{"role": "user", "content": "Hello"}],
}


def chat_frames() -> list[bytes]:
    """`openai/chat_stream.sse` as one chunk per frame.

    0 is the role chunk, 1-9 the content deltas ("Hello", "!", " How", ...), 10 the
    `finish_reason` chunk, 11 the usage-only chunk (`"choices":[]`) and 12 `[DONE]`.
    """
    return sse_frames(fixture_text("openai/chat_stream.sse"))


def anthropic_frame(event: str, data: dict[str, Any]) -> bytes:
    """One Messages stream event as an SSE frame."""
    return f"event: {event}\ndata: {json.dumps({'type': event, **data})}\n\n".encode()


def chat_request(*, stream: bool = True, **extra: Any) -> GatewayRequest:
    """A chat completion call; `extra` is merged into the body (`stream_options`, say)."""
    body = {**CHAT_BODY, **({"stream": True} if stream else {}), **extra}
    return GatewayRequest(surface="chat_completions", body=body, stream=stream)


def messages_request() -> GatewayRequest:
    return GatewayRequest(surface="messages", body=MESSAGES_BODY, stream=True)


def with_route(context: GatewayContext, **updates: Any) -> GatewayContext:
    """`context` with its route's fields replaced (`retry`, `fallback`, `targets`, `timeout_ms`).

    Not validated, so a test can use a budget the API would refuse, such as 200 ms.
    """
    return dataclasses.replace(context, route=context.route.model_copy(update=updates))


async def read_chunks(stream: ByteStream, count: int) -> list[bytes]:
    """The next `count` chunks of the client's stream."""
    return [await anext(stream) for _ in range(count)]


async def read_to_end(stream: ByteStream) -> list[bytes]:
    """Every chunk the client's stream yields until it ends."""
    return [chunk async for chunk in stream]


async def key_minute_counts(
    sessions: async_sessionmaker[AsyncSession], key_id: uuid.UUID
) -> tuple[int, int]:
    """`(requests, tokens)` counted for the key in `gateway_key_minutes`, every minute summed."""
    async with sessions() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        row = await db.execute(
            text(
                "SELECT coalesce(sum(requests), 0), coalesce(sum(tokens), 0)"
                " FROM gateway_key_minutes WHERE key_id = :id"
            ),
            {"id": str(key_id)},
        )
        requests, tokens = row.one()
        return int(requests), int(tokens)
