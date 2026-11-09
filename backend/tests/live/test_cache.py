"""The 2 s shared answer cache behind the live dashboard routes.

Failure modes: fifty tabs refreshing on one `changed` event each cost a database read, an answer
outlives the data it was computed from (a new marker or the 2 s bound), a failed read is served
to later callers, the cache grows without bound, and a cached response loses the headers the
route set.
"""

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.live.cache import AnswerCache
from app.metrics import service
from tests.helpers import create_workspace


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Counter:
    def __init__(self, value: Any = "answer") -> None:
        self.calls = 0
        self.value = value

    async def __call__(self) -> Any:
        self.calls += 1
        await asyncio.sleep(0.01)
        return self.value


KEY = ("/overview", uuid.uuid4(), (), "Access", (1, Decimal(1)))


async def test_fifty_concurrent_identical_requests_compute_once() -> None:
    cache = AnswerCache(max_entries=10)
    compute = Counter()

    results = await asyncio.gather(*(cache.get_or_compute(KEY, compute) for _ in range(50)))

    assert results == ["answer"] * 50
    assert compute.calls == 1


async def test_new_marker_misses_cache() -> None:
    cache = AnswerCache(max_entries=10)
    compute = Counter()

    await cache.get_or_compute(KEY, compute)
    await cache.get_or_compute((*KEY[:-1], (2, Decimal(1))), compute)

    assert compute.calls == 2


async def test_entry_expires_after_ttl() -> None:
    clock = Clock()
    cache = AnswerCache(max_entries=10, clock=clock)
    compute = Counter()

    await cache.get_or_compute(KEY, compute)
    clock.now += 1.9
    await cache.get_or_compute(KEY, compute)
    assert compute.calls == 1

    clock.now += 0.2
    await cache.get_or_compute(KEY, compute)
    assert compute.calls == 2


async def test_expired_entries_are_evicted_on_insert() -> None:
    clock = Clock()
    cache = AnswerCache(max_entries=10, clock=clock)
    keys = [(*KEY[:-1], (n, Decimal(0))) for n in range(3)]
    await cache.get_or_compute(keys[0], Counter())
    await cache.get_or_compute(keys[1], Counter())

    clock.now += 2.1
    await cache.get_or_compute(keys[2], Counter())

    assert list(cache._entries) == [keys[2]]


async def test_errors_are_not_cached() -> None:
    cache = AnswerCache(max_entries=10)
    calls = 0

    async def failing() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise ValueError("database down")

    results = await asyncio.gather(
        *(cache.get_or_compute(KEY, failing) for _ in range(5)), return_exceptions=True
    )

    assert calls == 1
    assert all(isinstance(result, ValueError) for result in results)
    assert await cache.get_or_compute(KEY, Counter("fresh")) == "fresh"


async def test_cancelled_leader_hands_over_to_a_waiter() -> None:
    cache = AnswerCache(max_entries=10)
    compute = Counter()
    leader = asyncio.create_task(cache.get_or_compute(KEY, compute))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(cache.get_or_compute(KEY, compute))
    await asyncio.sleep(0)

    leader.cancel()

    assert await waiter == "answer"
    assert compute.calls == 2


async def test_lru_bound() -> None:
    cache = AnswerCache(max_entries=2)
    compute = Counter()
    keys = [(*KEY[:-1], (n, Decimal(0))) for n in range(3)]

    for key in keys:
        await cache.get_or_compute(key, compute)
    await cache.get_or_compute(keys[2], compute)  # newest: kept
    assert compute.calls == 3
    await cache.get_or_compute(keys[0], compute)  # oldest: evicted
    assert compute.calls == 4


async def watch(app: Any, project_id: str) -> Any:
    """Subscribe to the project and wait for the hub's first marker read."""
    hub = app.state.live_hub
    subscription = hub.subscribe(uuid.UUID(project_id))
    async with asyncio.timeout(3):
        while hub.marker(uuid.UUID(project_id)) is None:  # noqa: ASYNC110 - no event to wait on
            await asyncio.sleep(0.02)
    return subscription


async def test_overview_cached_response_has_same_headers(
    app: Any, browser_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.live_hub, "_interval", 0.05)
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    base = f"/api/v1/projects/{workspace.project_id}/metrics"
    window = "?from=2026-11-01T00:00:00Z&to=2026-11-02T00:00:00Z"
    calls = {"overview": 0, "timeseries": 0}
    for name in calls:
        real = getattr(service, name)

        async def counted(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            calls[_name] += 1
            return await _real(*args, **kwargs)

        monkeypatch.setattr(service, name, counted)

    # Nobody watches the project: no marker, so nothing is cached.
    for _ in range(2):
        assert (await workspace.owner.get(base + "/overview" + window)).status_code == 200
    assert calls["overview"] == 2

    subscription = await watch(app, workspace.project_id)
    try:
        overviews = [await workspace.owner.get(base + "/overview" + window) for _ in range(2)]
        series = [await workspace.owner.get(base + "/timeseries" + window) for _ in range(2)]
    finally:
        subscription.close()

    assert calls == {"overview": 3, "timeseries": 1}
    for first, second in (overviews, series):
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
    assert series[0].headers["Aggregate-Status"] == series[1].headers["Aggregate-Status"]
    assert series[0].headers.get("Aggregate-Oldest-Pending-At") == series[1].headers.get(
        "Aggregate-Oldest-Pending-At"
    )
