"""A fixture status-page incident, served on a local HTTP server and stored through the product's
own poller, for `provider_incident:with_incident`.

The poller only fetches its adapters' constant `https://status.<host>/` URLs. The harness keeps
that URL and the poller's client (`build_status_client`, with its egress checks) and swaps only
the transport underneath: `LocalFeedTransport` sends the Anthropic feed request to a loopback
server that answers with one generated Statuspage incident. Everything after the fetch (parse,
upsert, source row) is the product's code path, the same one the scheduled job runs.

The incident is real-shaped but marked as a fixture: its title starts with "Spanlight Doctor
harness fixture", its external id with `spanlight-doctor-`, and it is already resolved when it
is stored, so the local stack's Provider health page never shows it as ongoing.

The poller (`app.provider_status.poller.poll_one`) is resolved by name when the live run seeds
the incident, so the offline tests of this module never depend on it.
"""

import asyncio
import importlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Self

import httpx

from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.provider_status.adapters.anthropic import anthropic

POLLER = "app.provider_status.poller"
STATUS_CLIENT = "app.provider_status.http"
FEED_HOST = httpx.URL(anthropic.url).host
FEED_PATH = httpx.URL(anthropic.url).path
API_COMPONENT = "Claude API (api.anthropic.com)"
FIXTURE_PREFIX = "spanlight-doctor-"


def fixture_incident_id(nonce: str) -> str:
    """The fixture's Statuspage id: one plain path segment, recognisable as a harness row."""
    return f"{FIXTURE_PREFIX}{nonce}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def fixture_feed(
    *, external_id: str, title: str, started_at: datetime, resolved_at: datetime
) -> bytes:
    """A Statuspage v2 `incidents.json` with one resolved, API-affecting major incident."""
    incident = {
        "id": external_id,
        "name": title,
        "status": "resolved",
        "impact": "major",
        "created_at": _iso(started_at),
        "started_at": _iso(started_at),
        "updated_at": _iso(resolved_at),
        "resolved_at": _iso(resolved_at),
        "components": [{"name": API_COMPONENT}],
        "incident_updates": [
            {
                "body": "Fixture incident seeded by the Spanlight Doctor accuracy harness.",
                "created_at": _iso(resolved_at),
            }
        ],
    }
    document = {"page": {"id": "spanlight-doctor-harness"}, "incidents": [incident]}
    return json.dumps(document).encode()


class FeedServer:
    """A loopback HTTP server that answers `FEED_PATH` with `body` (404 otherwise) and records
    the paths it was asked for. Use as a context manager; it serves on a background thread."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.paths: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        feed = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                feed.paths.append(self.path)
                found = self.path == FEED_PATH
                body = feed.body if found else b"{}"
                self.send_response(200 if found else 404)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return  # quiet: the harness prints its own one-line outcome

        return Handler

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class LocalFeedTransport(httpx.AsyncBaseTransport):
    """Sends requests for the Anthropic feed host to the loopback `FeedServer`; any other host
    gets a 404 without a connection, so the poll can reach nothing else."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != FEED_HOST:
            return httpx.Response(404, request=request)
        local = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        forwarded = httpx.Request(request.method, local, headers=request.headers)
        return await self._inner.handle_async_request(forwarded)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _poll_fixture(port: int, now: datetime) -> tuple[str, int]:
    poller = importlib.import_module(POLLER)
    status_http = importlib.import_module(STATUS_CLIENT)
    engine = create_engine(Settings().database_url, pool_size=1, max_overflow=0)
    try:
        sessions = create_session_factory(engine)
        transport = LocalFeedTransport(port)
        async with (
            status_http.build_status_client(transport=transport) as client,
            sessions() as db,
        ):
            result = await poller.poll_one(client, db, anthropic, now=now)
    finally:
        await engine.dispose()
    return str(result.outcome.value), int(result.upserted)


def seed_incident(feed: bytes, *, now: datetime) -> tuple[str, int]:
    """Serve `feed` locally and poll it once through the product's poller: `(outcome,
    upserted)`. Needs the local database (`backend/.env`), so it runs only in the live run."""
    with FeedServer(feed) as server:
        return asyncio.run(_poll_fixture(server.port, now))
