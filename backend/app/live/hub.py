"""One per api process: watches the change markers of projects with open dashboard tabs.

A single task reads every watched project's marker once per interval (one query, however many
tabs) and wakes the subscribers of each project whose marker differs from the last one seen.
With no subscribers it runs no query. One read per interval also bounds announcements to one
per project per interval, and a subscriber's queue holds at most one pending wake-up.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from decimal import Decimal
from types import TracebackType
from typing import Self

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import process_shutdown
from app.core.observability import LIVE_EVENTS, LIVE_POLL_DURATION, LIVE_POLL_FAILURES, LIVE_STREAMS
from app.live.markers import Marker, read_markers

logger = structlog.get_logger(__name__)

QUERY_BUDGET_SECONDS = 1.0
NO_MARKER: Marker = (0, Decimal(0))

Reader = Callable[[list[uuid.UUID]], Awaitable[dict[uuid.UUID, Marker]]]


class LiveBusyError(Exception):
    """This process already holds `max_streams` subscriptions."""


class Subscription:
    def __init__(self, hub: "LiveHub", project_id: uuid.UUID) -> None:
        self._hub = hub
        self.project_id = project_id
        self._queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self.closed = False
        self.evicted = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._hub._remove(self)

    def close(self) -> None:
        """End the subscription now: free its slot and wake its stream, which sees `closed`."""
        self.closed = True
        self._hub._remove(self)
        self._wake()

    def evict(self) -> None:
        """Close to make room for a newer stream of the same session; the stream says so."""
        self.evicted = True
        self.close()

    def _wake(self) -> None:
        # A full queue means the tab already has a refresh pending.
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def next_event(self, timeout: float) -> bool:  # noqa: ASYNC109 - the stream's heartbeat wait
        """True when the project changed, False when `timeout` seconds passed without a change."""
        try:
            async with asyncio.timeout(timeout):
                await self._queue.get()
        except TimeoutError:
            return False
        return True


class LiveHub:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval: float = 1.0,
        max_streams: int,
        reader: Reader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval
        self._max_streams = max_streams
        self._reader = reader or self._read
        self._subscribers: dict[uuid.UUID, set[Subscription]] = {}
        # Only watched projects: a marker kept after its last subscriber left would go stale.
        self._last: dict[uuid.UUID, Marker] = {}
        self._streams = 0
        self._task: asyncio.Task[None] | None = None
        self._closer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="live-hub")
        process = process_shutdown.current.get()
        if process is not None:
            # Uvicorn joins open requests before the lifespan stops the hub: end the streams at
            # the shutdown signal, or each would hold the process until the work deadline.
            self._closer = asyncio.create_task(self._close_on(process.stop), name="live-hub-close")

    async def _close_on(self, stop: asyncio.Event) -> None:
        await stop.wait()
        self._close_all()

    def _close_all(self) -> None:
        for subscribers in list(self._subscribers.values()):
            for subscription in list(subscribers):
                subscription.close()

    async def stop(self) -> None:
        self._close_all()
        if self._closer is not None:
            self._closer.cancel()
            with suppress(asyncio.CancelledError):
                await self._closer
            self._closer = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def subscribe(self, project_id: uuid.UUID) -> Subscription:
        """Watch a project; the first read after subscribing sets the baseline and wakes no one."""
        if self._streams >= self._max_streams:
            raise LiveBusyError
        subscription = Subscription(self, project_id)
        self._subscribers.setdefault(project_id, set()).add(subscription)
        self._streams += 1
        LIVE_STREAMS.inc()
        return subscription

    async def prime(self, project_id: uuid.UUID) -> None:
        """Set a watched project's baseline now, not at the next poll, so a change in the first
        second of a stream wakes it. Failures are counted and left to the next poll."""
        if project_id in self._last or project_id not in self._subscribers:
            return
        try:
            markers = await asyncio.wait_for(self._reader([project_id]), QUERY_BUDGET_SECONDS)
        except Exception as exc:  # noqa: BLE001 - the stream opens anyway; the poll sets it later
            LIVE_POLL_FAILURES.inc()
            logger.warning("live_prime_failed", error_type=type(exc).__name__)
            return
        if project_id in self._subscribers:  # its last subscriber may have left during the read
            self._last.setdefault(project_id, markers.get(project_id, NO_MARKER))

    def marker(self, project_id: uuid.UUID) -> Marker | None:
        """The last marker read for a watched project; None before its first read or unwatched."""
        return self._last.get(project_id)

    def _remove(self, subscription: Subscription) -> None:
        subscribers = self._subscribers.get(subscription.project_id)
        if subscribers is None or subscription not in subscribers:
            return
        subscribers.discard(subscription)
        self._streams -= 1
        LIVE_STREAMS.dec()
        if not subscribers:
            del self._subscribers[subscription.project_id]
            self._last.pop(subscription.project_id, None)

    async def _read(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Marker]:
        async with self._session_factory() as db:
            return await read_markers(db, ids)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self._subscribers:
                await self._poll(list(self._subscribers))

    async def _poll(self, ids: list[uuid.UUID]) -> None:
        started = time.perf_counter()
        try:
            markers = await asyncio.wait_for(self._reader(ids), QUERY_BUDGET_SECONDS)
        except Exception as exc:  # noqa: BLE001 - keep every subscription, retry next interval
            LIVE_POLL_FAILURES.inc()
            logger.warning("live_poll_failed", projects=len(ids), error_type=type(exc).__name__)
            return
        finally:
            LIVE_POLL_DURATION.observe(time.perf_counter() - started)
        for project_id in ids:
            subscribers = self._subscribers.get(project_id)
            if subscribers is None:  # its last subscriber left during the read
                continue
            current = markers.get(project_id, NO_MARKER)
            previous = self._last.get(project_id)
            self._last[project_id] = current
            if previous is not None and previous != current:
                LIVE_EVENTS.inc()
                for subscription in subscribers:
                    subscription._wake()
