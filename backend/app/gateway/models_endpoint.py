"""`GET /gw/v1/models`: the models a gateway key can ask for.

When the key's `allowed_models` or the route's alias names (the union across its targets) are
non-empty, the gateway answers itself with their sorted union, in the OpenAI list shape or, for
a client that sends `anthropic-version`, the Anthropic one. Otherwise the request goes to the
first target of the route whose provider speaks that envelope (`GET {base}/models` for OpenAI
and compatible servers, `GET https://api.anthropic.com/v1/models`) and the answer is passed
through unchanged, like any upstream response.

No span is recorded and no tokens are counted: listing models is not a model call. The upstream
request goes through the shared client, so the egress rules apply as on every other call.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.core.crypto import CryptoError
from app.db.models import ProviderKind
from app.gateway.adapters import UpstreamResponse, adapter_for
from app.gateway.context import UPSTREAM_ERROR_CODE
from app.gateway.credentials import decrypt_api_key
from app.gateway.errors import (
    Envelope,
    Surface,
    internal,
    no_compatible_target,
    upstream_redirect,
    upstream_unreachable,
)
from app.gateway.gemini import valid_model_id
from app.gateway.key_context import KeyContext
from app.gateway.routing import compatible_targets
from app.gateway.upstream_io import classify_failure, close_quietly, read_all

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

_PROVIDERS_BY_ENVELOPE: dict[Envelope, frozenset[ProviderKind]] = {
    "openai": frozenset(ProviderKind) - {ProviderKind.ANTHROPIC},
    "anthropic": frozenset({ProviderKind.ANTHROPIC}),
    "google": frozenset({ProviderKind.GEMINI}),
}


@dataclass(frozen=True)
class ModelsAnswer:
    status: int
    body: bytes
    headers: dict[str, str]
    upstream_error: bool = False


def listed_models(key: KeyContext) -> list[str]:
    """The key's allowed models and the route's alias names, sorted; empty if there are none."""
    names = set(key.key.allowed_models)
    for target in key.route.targets:
        names.update(target.model_aliases)
    return sorted(names)


def local_body(models: list[str], envelope: Envelope) -> dict[str, Any]:
    """The model list in the envelope's shape. `models` is not empty."""
    if envelope == "google":
        return {
            "models": [
                {"name": f"models/{model}", "supportedGenerationMethods": ["generateContent"]}
                for model in models
            ]
        }
    if envelope == "anthropic":
        return {
            "data": [
                {
                    "type": "model",
                    "id": model,
                    "display_name": model,
                    "created_at": "1970-01-01T00:00:00Z",
                }
                for model in models
            ],
            "has_more": False,
            "first_id": models[0],
            "last_id": models[-1],
        }
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": 0, "owned_by": "spanlight"}
            for model in models
        ],
    }


async def answer_models(
    key: KeyContext,
    envelope: Envelope,
    *,
    http: httpx.AsyncClient,
    settings: "Settings",
    client_headers: Mapping[str, str],
    request_id: str,
    pagination: Mapping[str, str | int] | None = None,
) -> ModelsAnswer:
    """The answer to `GET /models`. Raises `GatewayError` for a failure the gateway names."""
    base = {"X-Request-ID": request_id}
    models = _gemini_models(key) if envelope == "google" else listed_models(key)
    if models or (envelope == "google" and key.key.allowed_models):
        body = json.dumps(local_body(models, envelope), separators=(",", ":")).encode()
        return ModelsAnswer(200, body, {**base, "content-type": "application/json"})
    return await _forward(
        key,
        envelope,
        http=http,
        settings=settings,
        client_headers=client_headers,
        request_id=request_id,
        headers=base,
        pagination=pagination,
    )


def _gemini_models(key: KeyContext) -> list[str]:
    targets = compatible_targets(key.route, "gemini_models", key.credentials, key.route_name)
    names = set(key.key.allowed_models) or {
        name for index in targets for name in key.route.targets[index].model_aliases
    }
    return sorted(name for name in names if valid_model_id(name))


async def _forward(
    key: KeyContext,
    envelope: Envelope,
    *,
    http: httpx.AsyncClient,
    settings: "Settings",
    client_headers: Mapping[str, str],
    request_id: str,
    headers: dict[str, str],
    pagination: Mapping[str, str | int] | None,
) -> ModelsAnswer:
    providers = _PROVIDERS_BY_ENVELOPE[envelope]
    surface: Surface = "gemini_models" if envelope == "google" else "models"
    indices = [
        index
        for index in compatible_targets(key.route, surface, key.credentials, key.route_name)
        if key.credentials[key.route.targets[index].credential_id].provider in providers
    ]
    if not indices:
        raise no_compatible_target(key.route_name, surface)
    credential = key.credentials[key.route.targets[indices[0]].credential_id]
    adapter = adapter_for(credential.provider)
    try:
        api_key = decrypt_api_key(credential, settings=settings)
    except CryptoError as failure:
        logger.warning(
            "gateway.credential_unreadable",
            credential_id=str(credential.id),
            error_type=type(failure).__name__,
        )
        raise internal(request_id) from None
    upstream = adapter.prepare(credential, surface, pagination or {}, client_headers, api_key)
    timeout_ms = key.route.timeout_ms
    response: UpstreamResponse | None = None
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            response = await adapter.send(http, upstream, timeout_ms / 1000)
            if 300 <= response.status < 400:
                await response.aclose()
                raise upstream_redirect()
            body = await read_all(response)
    except BaseException as error:
        await close_quietly(response)  # whatever happened, never leave the connection open
        classified = classify_failure(error, timeout_ms)
        if classified is None:
            raise
        raise (classified.error or upstream_unreachable(1)) from None
    answer_headers = {**dict(response.headers), **headers, "X-Spanlight-Attempts": "1"}
    failed = not 200 <= response.status < 300
    if failed:
        answer_headers["X-Spanlight-Code"] = UPSTREAM_ERROR_CODE
    elif credential.provider is ProviderKind.TOGETHER:
        body = _together_models(body)
    return ModelsAnswer(response.status, body, answer_headers, upstream_error=failed)


def _together_models(body: bytes) -> bytes:
    """Together can return a bare model array; OpenAI clients require a list envelope."""
    try:
        models = json.loads(body)
    except (ValueError, RecursionError):
        return body
    if not isinstance(models, list):
        return body
    return json.dumps({"object": "list", "data": models}, separators=(",", ":")).encode()
