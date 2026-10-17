"""Helpers for the provider fetcher tests: a scripted provider API behind the vetted client.

- `ScriptedApi`: an async `httpx.MockTransport` handler that answers each request with the next
  `Reply` scripted for its path (or `default` once a path's script is used up) and keeps every
  request in `requests`. An unscripted request fails the test.
- `Reply`: a status, a JSON or raw body, headers, an optional delay before answering and
  `chunked=True` to send the body without a `Content-Length`.
- `vetted_client(settings, api)`: `build_http_client` over the scripted API, with
  `public_resolver` as DNS so the egress check lets requests through.
- `page(buckets, ...)` and `fixture_page(name)`: provider pages built from the Task 8 fixtures.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.core.egress import Resolver
from app.gateway.http import build_http_client
from tests.gateway_support import public_resolver

FIXTURES = Path(__file__).parent / "fixtures"
# The two days the fixtures cover, and the model patterns their lines map to.
DAY_FROM = date(2026, 10, 1)
DAY_TO = date(2026, 10, 2)
PATTERNS = ("claude-sonnet-4-5", "claude-opus-4-1", "gpt-4o")

ANTHROPIC_COST_PATH = "/v1/organizations/cost_report"
ANTHROPIC_USAGE_PATH = "/v1/organizations/usage_report/messages"
OPENAI_COST_PATH = "/v1/organization/costs"
OPENAI_USAGE_PATH = "/v1/organization/usage/completions"

# Admin keys the scripted API sees; nothing else may.
ANTHROPIC_KEY = "sk-ant-admin01-Q7xv93LmZp4RtKc8WnBy2HdJfE6gUaS1"
OPENAI_KEY = "sk-admin-Vb3Nq8ZtLx5MwPc1RkYh7GdJs2FeUa9T"


@dataclass
class Reply:
    status: int = 200
    json: Any = None
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0
    chunked: bool = False


@dataclass
class ScriptedApi:
    routes: dict[str, list[Reply]] = field(default_factory=dict)
    default: Reply | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        script = self.routes.get(request.url.path)
        reply = script.pop(0) if script else self.default
        if reply is None:
            raise AssertionError(f"unscripted request to {request.url.path}")
        if reply.delay_s:
            await asyncio.sleep(reply.delay_s)
        body = reply.body if reply.body is not None else json.dumps(reply.json).encode()
        if reply.chunked:
            return httpx.Response(reply.status, headers=reply.headers, content=_chunks(body))
        return httpx.Response(reply.status, headers=reply.headers, content=body)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    for start in range(0, len(body), 64 * 1024):
        yield body[start : start + 64 * 1024]


def vetted_client(
    settings: Settings, api: ScriptedApi, resolver: Resolver = public_resolver
) -> httpx.AsyncClient:
    return build_http_client(settings, transport=httpx.MockTransport(api), resolver=resolver)


def fixture_page(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return loaded


def page(
    buckets: Sequence[Mapping[str, Any]], *, has_more: bool = False, next_page: str | None = None
) -> dict[str, Any]:
    return {"data": list(buckets), "has_more": has_more, "next_page": next_page}


def split_pages(name: str, token: str = "page_two") -> list[Reply]:  # noqa: S107 - a page token
    """The fixture's buckets as two pages: the first bucket, then the rest."""
    buckets = fixture_page(name)["data"]
    return [
        Reply(json=page(buckets[:1], has_more=True, next_page=token)),
        Reply(json=page(buckets[1:])),
    ]


def single_page(name: str) -> list[Reply]:
    return [Reply(json=page(fixture_page(name)["data"]))]
