"""The dashboard's change stream: `GET /projects/{project_id}/live`, server-sent events.

A signed-in browser opens one per tab and refetches when it hears `changed`. The stream says
only that the project changed, with a counter of its own (`{"seq": n}`, from 1): the hub's
markers are platform-wide generations, and passing one on would leak ingest volume.

Session cookie only. Authorization is checked when the stream opens and again every
`RECHECK_SECONDS`, with the lookups every request uses, so sign-out, an expired session or a
removed membership ends it. Limits: `MAX_STREAMS_PER_SESSION` per session (a new one closes
the oldest, which ends with `event: evicted` so its tab stops reconnecting) and
`LIVE_MAX_STREAMS` per process (then 503 `LIVE_BUSY`).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, MutableMapping
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.api.deps import (
    Access,
    CurrentPrincipal,
    DbSession,
    SettingsDep,
    _member_access,
    require,
    session_cookie_name,
    utcnow,
)
from app.api.principals import SessionPrincipal
from app.core.errors import ProblemError, not_found
from app.core.permissions import Permission
from app.live.hub import LiveBusyError, LiveHub, Subscription
from app.services.sessions import resolve_session

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["live"])

RETRY_MILLISECONDS = 3000
HEARTBEAT_SECONDS = 20.0
RECHECK_SECONDS = 60.0
MAX_STREAMS_PER_SESSION = 3

SessionStreams = MutableMapping[uuid.UUID, list[Subscription]]


async def live_session(principal: CurrentPrincipal) -> SessionPrincipal:
    """The stream is for the dashboard: an API key or an access token is refused."""
    if not isinstance(principal, SessionPrincipal):
        raise ProblemError(
            403,
            "LIVE_SESSION_ONLY",
            "Live updates are for signed-in browsers; API keys and access tokens cannot use them.",
        )
    return principal


class _LiveResponse(StreamingResponse):
    """Runs `release` however the response ends, even if its body never started."""

    def __init__(self, body: AsyncIterator[str], release: Callable[[], None]) -> None:
        super().__init__(
            body,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self.release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


@router.get(
    "/projects/{project_id}/live",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "The change stream."},
        403: {"description": "Not a session (`LIVE_SESSION_ONLY`)."},
        503: {"description": "This process holds its maximum of streams (`LIVE_BUSY`)."},
    },
    summary="Stream change notices for a project",
)
async def live_stream(
    request: Request,
    session: Annotated[SessionPrincipal, Depends(live_session)],
    access: Annotated[Access, Depends(require(Permission.PROJECT_READ))],
    db: DbSession,
    settings: SettingsDep,
) -> StreamingResponse:
    """`event: changed` with `data: {"seq": n}` when the project has new data, `event: heartbeat`
    with `data: {}` every 20 s otherwise, and `retry: 3000` first. Refetch on `changed` and on
    reconnect. A stream closed to make room for a newer one of the same session ends with
    `event: evicted` and `data: {}`: close the `EventSource` and poll instead of reconnecting."""
    hub: LiveHub | None = request.app.state.live_hub
    if hub is None:
        raise not_found()
    project_id = access.require_project().id
    session_id = session.session.id
    token = request.cookies[session_cookie_name(settings)]
    # The stream outlives the request's transaction: give its connection back to the pool now.
    await db.close()
    streams: SessionStreams = request.app.state.live_sessions
    held = streams.get(session_id, [])
    if len(held) >= MAX_STREAMS_PER_SESSION:
        held.pop(0).evict()
    try:
        subscription = hub.subscribe(project_id)
    except LiveBusyError:
        raise ProblemError(
            503,
            "LIVE_BUSY",
            "Too many live streams on this server. Try again shortly.",
            headers={"Retry-After": "30"},
        ) from None
    streams.setdefault(session_id, []).append(subscription)

    def release() -> None:
        subscription.close()
        mine = streams.get(session_id)
        if mine is not None and subscription in mine:
            mine.remove(subscription)
            if not mine:
                del streams[session_id]

    # The baseline before the headers go out: a change right after the client's open refetch
    # wakes this stream instead of becoming the first poll's baseline.
    try:
        await hub.prime(project_id)
    except BaseException:  # cancelled while reading: the response never takes the slot
        release()
        raise
    return _LiveResponse(_events(request, subscription, token), release)


async def _events(request: Request, subscription: Subscription, token: str) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    seq = 0
    yield f"retry: {RETRY_MILLISECONDS}\n\n"
    heartbeat_at = loop.time() + HEARTBEAT_SECONDS
    check_at = loop.time() + RECHECK_SECONDS
    while True:
        changed = await subscription.next_event(max(0.0, min(heartbeat_at, check_at) - loop.time()))
        if subscription.closed:
            if subscription.evicted:  # a plain end would make `EventSource` reconnect
                yield "event: evicted\ndata: {}\n\n"
            return  # the hub is stopping: the client reconnects to the next process
        now = loop.time()
        if now >= check_at:
            allowed = await _still_allowed(request, token)
            if subscription.evicted:  # a newer tab took the slot during the recheck
                yield "event: evicted\ndata: {}\n\n"
                return
            if not allowed:
                return
            check_at = now + RECHECK_SECONDS
        if changed:
            seq += 1
            yield f'event: changed\ndata: {{"seq": {seq}}}\n\n'
            heartbeat_at = now + HEARTBEAT_SECONDS
        elif now >= heartbeat_at:
            # An event, not a comment: `EventSource` hides comments, and the browser counts
            # heartbeats to notice a stalled stream.
            yield "event: heartbeat\ndata: {}\n\n"
            heartbeat_at = now + HEARTBEAT_SECONDS


async def _still_allowed(request: Request, token: str) -> bool:
    """The session is still live and its user may still read the project: the same lookups as
    `authenticate_session` and `require(project:read)`, in a short transaction of their own."""
    app: Any = request.app
    try:
        async with app.state.session_factory() as db:
            active = await resolve_session(db, token, now=utcnow())
            if active is None:
                return False
            principal = SessionPrincipal(user=active.user, session=active.session)
            await _member_access(request, principal, db, Permission.PROJECT_READ)
    except ProblemError:
        return False
    except Exception as exc:  # noqa: BLE001 - cannot confirm access: end the stream, it reconnects
        logger.warning("live_recheck_failed", error_type=type(exc).__name__)
        return False
    return True
