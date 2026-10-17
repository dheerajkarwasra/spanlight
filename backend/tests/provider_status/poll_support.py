"""Shared setup for the poller tests: a scripted status-feed server and readers of the two tables.

`FeedServer` answers each feed URL from a queue of responses (the last one repeats) through an
`httpx.MockTransport`, and records every request it was sent. `client()` builds the real status
client around it, with a resolver that answers a public address, so the egress policy still runs.
"""

import copy
import json
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob
from app.provider_status.adapters.anthropic import anthropic
from app.provider_status.adapters.google import google
from app.provider_status.adapters.openai import openai
from app.provider_status.http import build_status_client

SessionFactory = async_sessionmaker[AsyncSession]
Row = dict[str, Any]
# A response with an in-memory body (repeatable), or a function that builds one per request.
Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
PUBLIC_ADDRESS = "93.184.216.34"
ANTHROPIC_FEED = "anthropic-incidents-2026-10-16.json"
OPENAI_FEED = "openai-incidents-2026-10-16.json"
OPENAI_RSS = "openai-history-2026-10-16.rss"
GOOGLE_FEED = "google-incidents-2026-10-16.json"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def anthropic_feed(*incidents: dict[str, Any]) -> bytes:
    """The recorded Anthropic feed holding only `incidents` (dicts in the feed's own shape)."""
    document = json.loads(fixture(ANTHROPIC_FEED))
    document["incidents"] = list(incidents)
    return json.dumps(document).encode()


def first_anthropic_incident(**changes: Any) -> dict[str, Any]:
    """The first recorded Anthropic incident, with `changes` applied."""
    incident = copy.deepcopy(json.loads(fixture(ANTHROPIC_FEED))["incidents"][0])
    incident.update(changes)
    return dict(incident)


async def public_resolver(host: str, port: int) -> Sequence[str]:
    return [PUBLIC_ADDRESS]


class FeedServer:
    """Answers feed URLs from scripted replies and records every request."""

    def __init__(self) -> None:
        self.replies: dict[str, list[Reply]] = {}
        self.requests: list[httpx.Request] = []

    def answer(self, url: str, *replies: Reply) -> None:
        self.replies[url] = list(replies)

    def serve_recorded_feeds(self) -> None:
        self.answer(anthropic.url, httpx.Response(200, content=fixture(ANTHROPIC_FEED)))
        self.answer(openai.url, httpx.Response(200, content=fixture(OPENAI_FEED)))
        self.answer(google.url, httpx.Response(200, content=fixture(GOOGLE_FEED)))

    def requests_to(self, url: str) -> list[httpx.Request]:
        return [request for request in self.requests if str(request.url) == url]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.replies.get(str(request.url))
        if not queue:
            return httpx.Response(404)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        response = reply(request) if callable(reply) else reply
        if not response.is_stream_consumed:
            return response  # a streamed body (an async generator): already like the network
        # `httpx.Response(content=bytes)` reads its body at once, which a network response never
        # does; answer with a fresh copy that streams it, so the poller's raw read works.
        return httpx.Response(
            response.status_code, headers=response.headers, stream=_Body(response.content)
        )

    def client(self, resolver: Any = public_resolver) -> httpx.AsyncClient:
        return build_status_client(transport=httpx.MockTransport(self._handle), resolver=resolver)


class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self._data), 65536):
            yield self._data[start : start + 65536]


async def chunks(size: int) -> AsyncIterator[bytes]:
    """`size` bytes in 64 KiB chunks, as a body with no content-length."""
    sent = 0
    while sent < size:
        step = min(65536, size - sent)
        sent += step
        yield b"x" * step


def task_context(session_factory: SessionFactory, settings: Any) -> TaskContext:
    job = ClaimedJob(
        id=1, kind="poll_provider_status", payload={}, attempts=1, max_attempts=1, fence=1
    )
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=lambda: NOW
    )


async def rows(session_factory: SessionFactory, sql: str, params: Row | None = None) -> list[Row]:
    async with session_factory() as db:
        result = await db.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]


async def execute(session_factory: SessionFactory, sql: str, params: Row | None = None) -> None:
    async with session_factory() as db:
        await db.execute(text(sql), params or {})
        await db.commit()


async def incidents(session_factory: SessionFactory, provider: str) -> list[Row]:
    return await rows(
        session_factory,
        "SELECT * FROM provider_incidents WHERE provider = :provider ORDER BY external_id",
        {"provider": provider},
    )


async def source(session_factory: SessionFactory, provider: str) -> Row:
    [row] = await rows(
        session_factory,
        "SELECT * FROM provider_status_sources WHERE provider = :provider",
        {"provider": provider},
    )
    return row
