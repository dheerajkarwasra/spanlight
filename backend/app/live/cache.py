"""A 2 s in-process answer cache for the dashboard reads that live tabs refetch together.

When a project changes, every open tab refetches the same routes at once. Identical requests
within 2 s on one api process share one database read. The project's change marker is part of
the key, so an answer never survives new data; the TTL bounds staleness otherwise. Callers look
up the cache only after the route's own `require(...)` has authorized them for the project.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from fastapi import Request, Response

from app.core.observability import LIVE_CACHE_HITS

TTL_SECONDS = 2.0

Key = tuple[Hashable, ...]


class AnswerCache:
    """Bounded FIFO with a TTL and single flight: concurrent callers with one key share one
    compute. Only successful results are stored; a failure reaches every waiter of that compute.
    """

    def __init__(
        self,
        max_entries: int,
        ttl: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._clock = clock
        self._entries: OrderedDict[Key, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Key, asyncio.Future[Any]] = {}

    async def get_or_compute[T](self, key: Key, compute: Callable[[], Awaitable[T]]) -> T:
        entry = self._entries.get(key)
        if entry is not None:
            if entry[0] > self._clock():
                LIVE_CACHE_HITS.inc()
                value: T = entry[1]
                return value
            del self._entries[key]
        while (flight := self._inflight.get(key)) is not None:
            try:
                shared: T = await asyncio.shield(flight)
            except asyncio.CancelledError:
                task = asyncio.current_task()
                # Only the leader was cancelled (its client left): compute in its place.
                if not flight.cancelled() or (task is not None and task.cancelling()):
                    raise
                continue
            LIVE_CACHE_HITS.inc()
            return shared
        return await self._lead(key, compute)

    async def _lead[T](self, key: Key, compute: Callable[[], Awaitable[T]]) -> T:
        flight: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = flight
        try:
            value = await compute()
        except asyncio.CancelledError:
            flight.cancel()
            raise
        except BaseException as error:
            flight.set_exception(error)
            flight.exception()  # retrieved: no "never retrieved" log when nobody waited
            raise
        finally:
            del self._inflight[key]
        flight.set_result(value)
        now = self._clock()
        # One TTL for all: insertion order is expiry order, so expired entries sit at the head.
        while self._entries and next(iter(self._entries.values()))[0] <= now:
            self._entries.popitem(last=False)
        self._entries.pop(key, None)
        self._entries[key] = (now + self._ttl, value)
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return value


async def cached_answer[T](
    request: Request,
    access: Any,
    compute: Callable[[dict[str, str]], Awaitable[T]],
    response: Response | None = None,
) -> T:
    """The route's answer, shared with identical requests while the project is watched live.

    `compute` fills the headers dict it is given; they are cached with the value and copied to
    `response`. Without a live hub, or for a project no tab watches, there is no marker and the
    cache is skipped.
    """
    project_id = access.require_project().id
    hub = request.app.state.live_hub
    marker = hub.marker(project_id) if hub is not None else None
    headers: dict[str, str]
    if marker is None:
        headers = {}
        value = await compute(headers)
    else:

        async def answer() -> tuple[T, dict[str, str]]:
            filled: dict[str, str] = {}
            return await compute(filled), filled

        # Raw query items, in order: a repeated parameter resolves to its last value.
        key = (
            request.url.path,
            project_id,
            tuple(request.query_params.multi_items()),
            type(access).__name__,
            marker,
        )
        value, headers = await request.app.state.live_cache.get_or_compute(key, answer)
    if response is not None:
        response.headers.update(headers)
    return value
