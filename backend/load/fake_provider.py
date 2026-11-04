"""A fake OpenAI-compatible provider for the gateway load test.

It answers `POST /v1/chat/completions` and `GET /v1/models` with fixed bodies, after
FAKE_PROVIDER_DELAY_MS milliseconds (default 0), and checks nothing about the request but its
`stream` flag: a streaming call gets the same answer as three server-sent events and `[DONE]`,
the delay spent before the first one. The gateway
overhead is the time a call spends in Spanlight, so the provider has to add as little and as
steady a time as possible: no model, no network, no randomness.

    uvicorn fake_provider:app --host 0.0.0.0 --port 9000

The load Compose file runs it in the backend image with this file mounted in.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

MODEL = "gpt-4o-mini"

CHAT_COMPLETION = json.dumps(
    {
        "id": "chatcmpl-load-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "This is a canned answer."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 24, "completion_tokens": 6, "total_tokens": 30},
    }
).encode()


def _chunk(delta: dict[str, str], finish_reason: str | None = None, **extra: object) -> bytes:
    choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
    body = {
        "id": "chatcmpl-load-test",
        "object": "chat.completion.chunk",
        "created": 1_700_000_000,
        "model": MODEL,
        "choices": [choice],
        **extra,
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


STREAM_CHUNKS = (
    _chunk({"role": "assistant", "content": "This is"}),
    _chunk({"content": " a canned answer."}),
    _chunk(
        {},
        "stop",
        usage={"prompt_tokens": 24, "completion_tokens": 6, "total_tokens": 30},
    ),
    b"data: [DONE]\n\n",
)

MODEL_LIST = json.dumps(
    {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "load-test"}]}
).encode()


def delay_seconds() -> float:
    """FAKE_PROVIDER_DELAY_MS as seconds: blank or unset is 0, and a bad value stops the start."""
    raw = os.environ.get("FAKE_PROVIDER_DELAY_MS", "").strip() or "0"
    milliseconds = float(raw)
    if milliseconds < 0:
        raise ValueError("FAKE_PROVIDER_DELAY_MS must not be negative")
    return milliseconds / 1000


DELAY_SECONDS = delay_seconds()


async def stream_chunks() -> AsyncIterator[bytes]:
    if DELAY_SECONDS:
        await asyncio.sleep(DELAY_SECONDS)
    for chunk in STREAM_CHUNKS:
        yield chunk


async def chat_completions(request: Request) -> Response:
    # Only the stream flag is read; the answer is canned either way.
    if b'"stream"' in (body := await request.body()) and json.loads(body).get("stream") is True:
        return StreamingResponse(stream_chunks(), media_type="text/event-stream")
    if DELAY_SECONDS:
        await asyncio.sleep(DELAY_SECONDS)
    return Response(CHAT_COMPLETION, media_type="application/json")


async def models(request: Request) -> Response:
    return Response(MODEL_LIST, media_type="application/json")


app = Starlette(
    routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/models", models, methods=["GET"]),
    ]
)
