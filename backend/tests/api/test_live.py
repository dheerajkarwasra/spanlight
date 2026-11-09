"""`GET /api/v1/projects/{project_id}/live`: the dashboard's change stream.

Failure modes: a stream that tells someone about a project they cannot read (an API key, a
token, a non-member, a member who was removed or signed out), a stream that leaks platform-wide
ingest volume, a stream that never ends (shutdown, eviction) and so holds a hub slot forever,
and a proxy or admission cap that buffers or starves the stream.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app import process_shutdown
from app.api.v1 import live
from app.config import Settings
from app.live.hub import Subscription
from app.main import create_app
from tests.asgi_harness import Message, call_asgi, is_last_body
from tests.helpers import (
    Browser,
    bearer,
    create_token,
    create_workspace,
    join_with_role,
    new_trace_id,
    span,
)

CADDYFILE = Path(__file__).resolve().parents[3] / "deploy" / "Caddyfile"


def live_path(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/live"


class Stream:
    """One open stream, driven through the ASGI harness so each message is seen as it is sent."""

    def __init__(self, app: Any, browser: Browser, project_id: str) -> None:
        cookie = "; ".join(f"{name}={value}" for name, value in browser.http.cookies.items())
        self.messages: asyncio.Queue[Message] = asyncio.Queue()
        self.task = asyncio.create_task(
            call_asgi(
                app,
                live_path(project_id),
                method="GET",
                headers=[("cookie", cookie)],
                on_message=self._received,
            )
        )

    async def _received(self, message: Message) -> None:
        self.messages.put_nowait(message)

    async def start(self) -> Message:
        message = await asyncio.wait_for(self.messages.get(), 3)
        assert message["type"] == "http.response.start", message
        return message

    async def next_body(self, timeout: float = 3) -> Message:  # noqa: ASYNC109 - test bound
        message = await asyncio.wait_for(self.messages.get(), timeout)
        assert message["type"] == "http.response.body", message
        return message

    async def ended(self, timeout: float = 3) -> bool:  # noqa: ASYNC109 - test bound
        """Whether the server ends the response within `timeout` seconds."""
        try:
            async with asyncio.timeout(timeout):
                while not is_last_body(await self.messages.get()):
                    pass
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


@asynccontextmanager
async def streaming(app: Any, browser: Browser, project_id: str) -> AsyncIterator[Stream]:
    stream = Stream(app, browser, project_id)
    try:
        yield stream
    finally:
        await stream.close()


async def opened(stream: Stream) -> None:
    """The response has started and the stream sent its `retry:` line."""
    start = await stream.start()
    assert start["status"] == 200, start
    assert (await stream.next_body())["body"] == b"retry: 3000\n\n"


async def baseline(app: Any, project_id: str) -> None:
    """Wait for the hub's first read of the project, so a later ingest is a change."""
    hub = app.state.live_hub
    async with asyncio.timeout(3):
        while hub.marker(uuid.UUID(project_id)) is None:  # noqa: ASYNC110 - no event to wait on
            await asyncio.sleep(0.02)


async def ingest(client: httpx.AsyncClient, api_key: str) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": [span(trace_id=new_trace_id())]}, headers=bearer(api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text


@pytest.fixture
def fast_hub(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state.live_hub, "_interval", 0.05)


@asynccontextmanager
async def custom_app(settings: Settings, **update: Any) -> AsyncIterator[tuple[Any, Browser]]:
    """An app with other settings and one signed-in browser on it."""
    application = create_app(settings.model_copy(update=update))
    async with application.router.lifespan_context(application):
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
            headers={"Origin": "http://testserver"},
        )
        async with http:
            signup = await http.post(
                "/api/v1/auth/signup",
                json={
                    "email": "solo@example.com",
                    "password": "correct horse battery",
                    "name": "S",
                },
            )
            assert signup.status_code == 201, signup.text
            yield application, Browser(http=http, user=signup.json())


async def test_live_404_for_non_member(browser_factory: Any) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    stranger = await browser_factory("stranger@example.com")

    response = await stranger.get(live_path(workspace.project_id))

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_live_403_for_api_key(browser_factory: Any, client: httpx.AsyncClient) -> None:
    owner = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    token = await create_token(owner, scope="read")

    for credential in (workspace.api_key, token["token"]):
        response = await client.get(live_path(workspace.project_id), headers=bearer(credential))
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "LIVE_SESSION_ONLY"


@pytest.mark.usefixtures("fast_hub")
async def test_live_streams_changed_after_ingest(
    app: Any, browser_factory: Any, client: httpx.AsyncClient
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        await baseline(app, workspace.project_id)
        await ingest(client, workspace.api_key)

        body = (await stream.next_body())["body"].decode()

    assert body == 'event: changed\ndata: {"seq": 1}\n\n'
    assert json.loads(body.split("data: ", 1)[1]) == {"seq": 1}
    assert "generation" not in body and "marker" not in body


async def test_live_heartbeat_is_an_event(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comment is invisible to `EventSource`; the browser counts heartbeats to find a stalled
    stream, so the heartbeat is a named event."""
    monkeypatch.setattr(live, "HEARTBEAT_SECONDS", 0.1)
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        body = (await stream.next_body())["body"].decode()

    assert body == "event: heartbeat\ndata: {}\n\n"


@pytest.mark.usefixtures("fast_hub")
async def test_ingest_into_other_project_sends_nothing(
    app: Any, browser_factory: Any, client: httpx.AsyncClient
) -> None:
    owner = await browser_factory("owner@example.com")
    watched = await create_workspace(owner)
    other = await create_workspace(await browser_factory("other@example.com"), org_name="Other")

    async with streaming(app, owner, watched.project_id) as stream:
        await opened(stream)
        await baseline(app, watched.project_id)
        await ingest(client, other.api_key)
        await asyncio.sleep(0.5)  # ten hub reads

        assert stream.messages.empty()


async def test_live_headers(app: Any, browser_factory: Any) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        start = await stream.start()

    headers = {name.decode(): value.decode() for name, value in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-store"
    assert headers["x-accel-buffering"] == "no"


async def test_live_fourth_stream_closes_oldest(app: Any, browser_factory: Any) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    owner, project_id = workspace.owner, workspace.project_id

    async with (
        streaming(app, owner, project_id) as first,
        streaming(app, owner, project_id) as second,
        streaming(app, owner, project_id) as third,
    ):
        for stream in (first, second, third):
            await opened(stream)
        async with streaming(app, owner, project_id) as fourth:
            await opened(fourth)

            assert await first.ended()
            assert not await second.ended(timeout=0.2)
            assert app.state.live_hub._streams == 3


async def test_live_evicted_stream_says_so_before_ending(app: Any, browser_factory: Any) -> None:
    """A plain end of stream makes `EventSource` reconnect, and that reconnect would evict the
    next tab in turn: the evicted stream ends with a terminal `evicted` event so the tab stops."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    owner, project_id = workspace.owner, workspace.project_id

    async with (
        streaming(app, owner, project_id) as first,
        streaming(app, owner, project_id) as second,
        streaming(app, owner, project_id) as third,
    ):
        for stream in (first, second, third):
            await opened(stream)
        async with streaming(app, owner, project_id) as fourth:
            await opened(fourth)

            assert (await first.next_body())["body"] == b"event: evicted\ndata: {}\n\n"
            assert await first.ended()


async def test_live_eviction_during_the_access_recheck_still_says_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer tab can evict the stream while it re-checks access; ending it plainly then would
    make `EventSource` reconnect and evict the next tab."""

    class _Hub:
        def _remove(self, subscription: Subscription) -> None:
            pass

    subscription = Subscription(_Hub(), uuid.uuid4())  # type: ignore[arg-type]

    async def evicted_meanwhile(request: Any, token: str) -> bool:
        subscription.evict()
        return False

    monkeypatch.setattr(live, "_still_allowed", evicted_meanwhile)
    monkeypatch.setattr(live, "RECHECK_SECONDS", 0)

    events = [event async for event in live._events(Any, subscription, "token")]

    assert events[-1] == "event: evicted\ndata: {}\n\n"


async def test_live_busy_returns_503(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    monkeypatch.setattr(app.state.live_hub, "_max_streams", 1)
    viewer = await browser_factory("viewer@example.com")
    await join_with_role(workspace.owner, viewer, workspace.org_id, "viewer")

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        response = await viewer.get(live_path(workspace.project_id))

    assert response.status_code == 503
    assert response.json()["code"] == "LIVE_BUSY"
    assert response.headers["retry-after"] == "30"


async def test_live_ends_after_membership_removed(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "RECHECK_SECONDS", 0.1)
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    viewer = await browser_factory("viewer@example.com")
    await join_with_role(workspace.owner, viewer, workspace.org_id, "viewer")

    async with streaming(app, viewer, workspace.project_id) as stream:
        await opened(stream)
        assert not await stream.ended(timeout=0.3)  # still a member: rechecks pass
        removed = await workspace.owner.delete(
            f"/api/v1/orgs/{workspace.org_id}/members/{viewer.user['id']}",
            json={"password": "correct horse battery"},
        )
        assert removed.status_code == 204, removed.text

        assert await stream.ended(timeout=2)


async def test_live_ends_after_sign_out(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "RECHECK_SECONDS", 0.1)
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        assert (await workspace.owner.post("/api/v1/auth/logout")).status_code == 204

        assert await stream.ended(timeout=2)


async def test_live_ends_when_hub_stops(app: Any, browser_factory: Any) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        await app.state.live_hub.stop()

        assert await stream.ended(timeout=1)


async def test_live_ends_at_the_shutdown_signal(app: Any, browser_factory: Any) -> None:
    """Uvicorn joins open requests before the lifespan stops the hub: the signal must end them."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    process = process_shutdown.current.get()
    assert process is not None

    async with streaming(app, workspace.owner, workspace.project_id) as stream:
        await opened(stream)
        process.stop.set()

        assert await stream.ended(timeout=1)


async def test_live_disabled_404_when_setting_off(settings: Settings) -> None:
    async with custom_app(settings, live_updates_enabled=False) as (_, browser):
        workspace = await create_workspace(browser)
        response = await browser.get(live_path(workspace.project_id))

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_open_stream_does_not_hold_an_admission_slot(settings: Settings) -> None:
    async with custom_app(settings, api_max_in_flight=1) as (application, browser):
        workspace = await create_workspace(browser)
        async with streaming(application, browser, workspace.project_id) as stream:
            await opened(stream)
            response = await browser.get(f"/api/v1/projects/{workspace.project_id}")

    assert response.status_code == 200, response.text


def test_caddy_streams_live_route_unbuffered() -> None:
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    block = caddyfile.split("@live path /api/v1/projects/*/live", 1)[1].split("\n\t}\n", 1)[0]

    assert "handle @live" in block
    assert "flush_interval -1" in block
    # Compression would hold events back until a buffer fills.
    assert "@compressible not path /gw/* /api/v1/projects/*/live" in caddyfile
