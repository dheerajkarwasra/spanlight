"""The LLM gateway: `/gw/v1/*`, called by OpenAI and Anthropic SDKs with a Spanlight gateway key.

The OpenAI SDK uses `base_url="<host>/gw/v1"`; the Anthropic SDK uses `base_url="<host>/gw"`
(it appends `/v1/messages`). A call is checked in this order, and the first check that refuses
answers in the provider envelope of the path (`app.api.gw_errors`):

1. the key, from `Authorization: Bearer spl_gw_…` or `x-api-key: spl_gw_…` (both present with
   different values is refused): parsed, found by its prefix, its secret compared in constant
   time (an unknown prefix still runs a comparison), then its project, route and credentials
   loaded (`UNAUTHORIZED`);
2. the organization ceiling, the key's requests per minute, then its tokens per minute
   (`RATE_LIMITED`); the request's transaction ends here, before any upstream call. A refused
   model call is recorded as a span of the key's project (`app.gateway.refusal_span`, queued,
   no database round trip); a refused `/models` call records nothing, like an admitted one;
3. the body: at most 10 MB (`PAYLOAD_TOO_LARGE`), a JSON object with a string `model`
   (`INVALID_REQUEST`);
4. `execute`, which applies the route (model allowlist, budget, cache, faults, retries and
   fallbacks) and answers with the provider's bytes or a gateway error.

A stream is answered as it arrives from the provider. However the response ends (the provider
finished, the client went away, sending failed), the stream is closed in a `finally`, which
closes the provider connection and writes the span. A `truncated_stream` fault ends the
response with a clean end of stream after the last forwarded frame: Starlette has no way to
abort a connection without raising through the server, so the truncation shows as a stream
without its final event (`[DONE]` or `message_stop`), which is what SDKs detect. Its span is an
error naming the fault.

None of the dashboard's middleware touches these paths: no cookies, CSRF or Origin check (they
are scoped to `/api/`), and the 1 MiB body limit is `/api/v1` only.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import Headers
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.api.gw_errors import request_id_of
from app.config import Settings
from app.core.body import (
    BodyTooLarge,
    ClientDisconnected,
    UndecodableBody,
    UnsupportedEncoding,
    read_bounded_body,
)
from app.core.observability import GATEWAY_REQUESTS
from app.core.security import GATEWAY_KEY_PREFIX, key_secret_matches, parse_key
from app.core.text import clean_text
from app.gateway.context import ByteStream, GatewayRequest, Received, context_for_key
from app.gateway.errors import (
    GatewayError,
    Surface,
    envelope_for,
    internal,
    invalid_key,
    invalid_request,
    request_too_large,
    surface_for_path,
)
from app.gateway.execute import execute
from app.gateway.key_context import KeyContext, find_key, load_key_context
from app.gateway.key_limits import check_limits
from app.gateway.models_endpoint import answer_models
from app.gateway.refusal_span import record_refusal
from app.gateway.runtime import GatewayRuntime
from app.gateway.trace_headers import TraceHeaders

router = APIRouter(prefix="/gw/v1", tags=["gateway"])

# "10 MB" in the error message. Caddy allows a little more (11 MiB), so this limit answers with
# the provider-shaped 413 rather than Caddy's plain one.
MAX_REQUEST_BYTES = 10 * 1024 * 1024
# A client that trickles its body would otherwise hold a request slot for as long as it likes.
BODY_READ_TIMEOUT_S = 30.0
# Compared against when the presented prefix matches no key, so an unknown key costs the same
# hash comparison as a known one with the wrong secret.
_DUMMY_SECRET_HASH = bytes(32)
# Client headers that are credentials. They are dropped before the request reaches `execute`,
# whose adapters copy only an allowlist upstream anyway.
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "cookie"})
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

_REQUEST_BODY_DOC: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {"model": {"type": "string"}, "stream": {"type": "boolean"}},
                    "additionalProperties": True,
                }
            }
        },
    }
}


def get_runtime(request: Request) -> GatewayRuntime:
    runtime = getattr(request.app.state, "gateway_runtime", None)
    if not isinstance(runtime, GatewayRuntime):
        raise internal(request_id_of(request))  # the lifespan did not run
    return runtime


def presented_key(headers: Headers) -> str:
    """The key the client sent. Raises `UNAUTHORIZED` when it sent none or two different ones.

    Both values come from the client, so comparing them with each other leaks nothing.
    """
    values: list[str] = []
    for value in headers.getlist("authorization"):
        scheme, _, credential = value.strip().partition(" ")
        values.append(credential.strip() if scheme.lower() == "bearer" else value.strip())
    values.extend(value.strip() for value in headers.getlist("x-api-key"))
    if len(set(values)) > 1:
        raise invalid_key(conflicting=True)
    if not values or not values[0]:
        raise invalid_key()
    return values[0]


async def _authenticate(request: Request, *, model_call: bool) -> KeyContext:
    """The key, after every limit passed. A model call checks the tokens window too, and its
    refusal leaves a span."""
    # The call's clocks start here, so its overhead includes authentication, limits and the body.
    request.state.gateway_received = Received.now(request.app.state.clock)
    runtime = get_runtime(request)
    raw = presented_key(request.headers)
    parsed = parse_key(raw, GATEWAY_KEY_PREFIX)
    if parsed is None:
        raise invalid_key()
    settings: Settings = runtime.settings
    async with runtime.sessions() as db, db.begin():
        key = await find_key(db, parsed.prefix)
        stored = key.secret_hash if key is not None else _DUMMY_SECRET_HASH
        if not key_secret_matches(parsed.secret, stored) or key is None:
            raise invalid_key()
        key_context = await load_key_context(db, key)
        if key_context is None:
            raise invalid_key()
        refusal = await check_limits(
            db,
            runtime.limiter,
            key=key,
            org_id=key_context.org_id,
            settings=settings,
            now=request.app.state.clock(),
            check_tokens=model_call,
        )
    # The transaction is over: nothing is held while the provider answers.
    if refusal is not None:
        if model_call:
            _record_refusal(request, runtime, key_context, refusal)
        raise refusal
    return key_context


def _record_refusal(
    request: Request, runtime: GatewayRuntime, key: KeyContext, refusal: GatewayError
) -> None:
    """Queue the refused call's span; the client's answer is the error handler's, unchanged."""
    call = GatewayRequest(
        surface=surface_for_path(request.url.path),
        body={},  # never read: the limits are checked before the body
        stream=False,
        trace=TraceHeaders.from_headers(request.headers),
        request_id=request_id_of(request),
        received=getattr(request.state, "gateway_received", None),
    )
    context = context_for_key(key, runtime, clock=request.app.state.clock)
    record_refusal(call, context, refusal)


async def authenticate_gateway_key(request: Request) -> KeyContext:
    """The key of a model call, after every limit passed."""
    return await _authenticate(request, model_call=True)


async def authenticate_models_key(request: Request) -> KeyContext:
    """The key of a `/models` call: the tokens window is not checked, nothing is consumed."""
    return await _authenticate(request, model_call=False)


GatewayKey = Annotated[KeyContext, Depends(authenticate_gateway_key)]
ModelsKey = Annotated[KeyContext, Depends(authenticate_models_key)]


async def read_model_call(request: Request) -> dict[str, Any]:
    """The body as a JSON object with a string `model`, at most `MAX_REQUEST_BYTES`."""
    try:
        async with asyncio.timeout(BODY_READ_TIMEOUT_S):
            raw = await read_bounded_body(request, MAX_REQUEST_BYTES)
    except TimeoutError:
        raise invalid_request("Request body was not sent in time.", param=None) from None
    except BodyTooLarge:
        raise request_too_large() from None
    except (UnsupportedEncoding, UndecodableBody):
        message = "Request body could not be decoded: send JSON, optionally gzip or deflate."
        raise invalid_request(message, param=None) from None
    except ClientDisconnected:
        # Nobody reads this answer; it keeps a client that left from being logged as our error.
        raise invalid_request("Request body was not fully sent.", param=None) from None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        raise invalid_request() from None
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise invalid_request()
    if clean_text(body["model"]) != body["model"]:
        # A NUL or a lone surrogate: no provider serves such a model, and Postgres cannot take
        # it as a query parameter (the budget and route checks match on the model name).
        raise invalid_request("'model' must not contain NUL or unpaired surrogate characters.")
    return body


def forwardable_headers(headers: Headers) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name not in _CREDENTIAL_HEADERS}


class GatewayStreamingResponse(StreamingResponse):
    """A provider stream, always closed when the response ends, however it ends."""

    def __init__(self, stream: ByteStream, *, status_code: int, headers: Mapping[str, str]):
        # No cache may keep a stream, and no proxy in front (nginx and the like) may buffer it:
        # tokens must reach the client as they arrive.
        super().__init__(stream, status_code=status_code, headers={**headers, **_STREAM_HEADERS})
        self._stream = stream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Covers a client gone before the first chunk and a send that failed: closing writes
            # the span now instead of whenever the generator is collected. Idempotent.
            await self._stream.aclose()


async def _serve(request: Request, surface: Surface, key: KeyContext) -> Response:
    runtime = get_runtime(request)
    body = await read_model_call(request)
    call = GatewayRequest(
        surface=surface,
        body=body,
        stream=body.get("stream") is True,
        trace=TraceHeaders.from_headers(request.headers),
        client_headers=forwardable_headers(request.headers),
        request_id=request_id_of(request),
        received=getattr(request.state, "gateway_received", None),
    )
    context = context_for_key(key, runtime, clock=request.app.state.clock)
    result = await execute(call, context)
    if result.stream is not None:
        return GatewayStreamingResponse(
            result.stream, status_code=result.status, headers=result.headers
        )
    return Response(result.body or b"", status_code=result.status, headers=result.headers)


@router.post("/chat/completions", openapi_extra=_REQUEST_BODY_DOC)
async def chat_completions(request: Request, key: GatewayKey) -> Response:
    """OpenAI Chat Completions through the key's route; streams when `stream` is true."""
    return await _serve(request, "chat_completions", key)


@router.post("/responses", openapi_extra=_REQUEST_BODY_DOC)
async def responses(request: Request, key: GatewayKey) -> Response:
    """OpenAI Responses through the key's route; streams when `stream` is true."""
    return await _serve(request, "responses", key)


@router.post("/messages", openapi_extra=_REQUEST_BODY_DOC)
async def messages(request: Request, key: GatewayKey) -> Response:
    """Anthropic Messages through the key's route; streams when `stream` is true."""
    return await _serve(request, "messages", key)


@router.get("/models")
async def models(request: Request, key: ModelsKey) -> Response:
    """The models the key can ask for, in the OpenAI shape or, with `anthropic-version`, the
    Anthropic one. Records no span and counts no tokens."""
    runtime = get_runtime(request)
    answer = await answer_models(
        key,
        envelope_for("models", request.headers),
        http=runtime.http,
        settings=runtime.settings,
        client_headers=forwardable_headers(request.headers),
        request_id=request_id_of(request),
    )
    GATEWAY_REQUESTS.labels("models", "upstream_error" if answer.upstream_error else "ok").inc()
    return Response(answer.body, status_code=answer.status, headers=answer.headers)


# Starlette answers a path that differs from a route only by a trailing slash with a bare 307 to
# that route; the gateway answers it with its provider-shaped 404, like any other unknown path.
@router.api_route(
    "/{path:path}/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def trailing_slash(path: str) -> Response:
    raise HTTPException(404)
