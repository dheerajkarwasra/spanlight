"""The live hub wakes a project's subscribers when its change marker moves, and only then.

Failure modes: a tab misses new data (a lower generation committed after a higher one, or an
ingest after the repair job deleted the row), a tab refetches when nothing changed, or the hub
queries the database while nobody is watching. All but the last test inject a fake marker
reader, so they need no database.
"""

import asyncio
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY

from app.live.hub import LiveBusyError, LiveHub
from app.live.markers import Marker, read_markers
from tests.helpers import bearer, create_workspace, new_trace_id, span

INTERVAL = 0.02


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """The fake-reader tests use no database; the DB test empties the tables itself."""


class FakeReader:
    def __init__(self) -> None:
        self.markers: dict[uuid.UUID, Marker] = {}
        self.calls = 0
        self.failures = 0

    async def __call__(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Marker]:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("database unavailable")
        return {pid: self.markers[pid] for pid in ids if pid in self.markers}


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110 - the hub exposes no event to wait on
            await asyncio.sleep(INTERVAL / 4)


async def started(reader: FakeReader, *, max_streams: int = 10, interval: float = INTERVAL) -> Any:
    hub = LiveHub(None, interval=interval, max_streams=max_streams, reader=reader)  # type: ignore[arg-type]
    await hub.start()
    return hub


async def test_emits_when_marker_changes() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (1, Decimal(10))
    hub = await started(reader)
    try:
        async with hub.subscribe(pid) as sub:
            await until(lambda: reader.calls >= 1)
            reader.markers[pid] = (2, Decimal(21))
            assert await sub.next_event(1.0) is True
            assert hub.marker(pid) == (2, Decimal(21))
    finally:
        await hub.stop()


async def test_emits_when_lower_generation_commits_late() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (1, Decimal(11))  # generation N+1 = 11 committed first
    hub = await started(reader)
    try:
        async with hub.subscribe(pid) as sub:
            await until(lambda: reader.calls >= 1)
            reader.markers[pid] = (2, Decimal(21))  # generation N = 10 appears late
            assert await sub.next_event(1.0) is True
    finally:
        await hub.stop()


async def test_row_deleted_after_ack_emits_once_then_new_ingest_emits() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (2, Decimal(21))
    hub = await started(reader)
    try:
        async with hub.subscribe(pid) as sub:
            await until(lambda: reader.calls >= 1)
            del reader.markers[pid]  # the repair job acked and deleted the dirty rows
            assert await sub.next_event(1.0) is True
            assert hub.marker(pid) == (0, Decimal(0))
            assert await sub.next_event(INTERVAL * 3) is False
            reader.markers[pid] = (1, Decimal(30))  # a new ingest after the deletion
            assert await sub.next_event(1.0) is True
    finally:
        await hub.stop()


async def test_subscribe_records_current_marker_no_initial_event() -> None:
    reader, pid, empty = FakeReader(), uuid.uuid4(), uuid.uuid4()
    reader.markers[pid] = (5, Decimal(50))
    hub = await started(reader)
    try:
        async with hub.subscribe(pid) as first, hub.subscribe(empty) as other:
            assert await first.next_event(INTERVAL * 3) is False
            assert await other.next_event(INTERVAL) is False
            assert hub.marker(pid) == (5, Decimal(50))
            assert hub.marker(empty) == (0, Decimal(0))
            async with hub.subscribe(pid) as late:
                assert await late.next_event(INTERVAL * 3) is False
        assert hub.marker(pid) is None
    finally:
        await hub.stop()


async def test_no_query_without_subscribers() -> None:
    reader = FakeReader()
    hub = await started(reader)
    try:
        await asyncio.sleep(INTERVAL * 3)
        assert reader.calls == 0
        async with hub.subscribe(uuid.uuid4()):
            await until(lambda: reader.calls >= 1)
        await asyncio.sleep(INTERVAL)
        calls = reader.calls
        await asyncio.sleep(INTERVAL * 3)
        assert reader.calls == calls
    finally:
        await hub.stop()


async def test_one_event_per_project_per_second() -> None:
    interval = 0.3
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (1, Decimal(1))
    hub = await started(reader, interval=interval)
    try:
        async with hub.subscribe(pid) as sub:
            await until(lambda: reader.calls >= 1)
            for n in (2, 3, 4):
                reader.markers[pid] = (n, Decimal(n * n))
                await asyncio.sleep(0.01)
            assert await sub.next_event(interval * 2) is True
            assert reader.calls == 2
            assert await sub.next_event(interval * 1.5) is False
    finally:
        await hub.stop()


async def test_busy_at_max_streams() -> None:
    hub = await started(FakeReader(), max_streams=2)
    pid = uuid.uuid4()
    try:
        async with hub.subscribe(pid), hub.subscribe(uuid.uuid4()):
            with pytest.raises(LiveBusyError):
                hub.subscribe(pid)
        async with hub.subscribe(pid):
            pass
    finally:
        await hub.stop()


async def test_query_failure_keeps_subscriptions() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (1, Decimal(1))
    hub = await started(reader)
    failures = REGISTRY.get_sample_value("spanlight_live_poll_failures_total") or 0.0
    try:
        async with hub.subscribe(pid) as sub:
            await until(lambda: reader.calls >= 1)
            reader.failures = 1
            calls = reader.calls
            await until(lambda: reader.calls == calls + 1)
            assert REGISTRY.get_sample_value("spanlight_live_poll_failures_total") == failures + 1
            assert await sub.next_event(0) is False
            assert hub.marker(pid) == (1, Decimal(1))
            reader.markers[pid] = (2, Decimal(3))
            assert await sub.next_event(1.0) is True
    finally:
        await hub.stop()


async def test_prime_sets_the_baseline_once() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (5, Decimal(50))
    hub = await started(reader, interval=60)
    try:
        async with hub.subscribe(pid):
            await hub.prime(pid)
            await hub.prime(pid)
            assert (hub.marker(pid), reader.calls) == ((5, Decimal(50)), 1)
        await hub.prime(pid)  # no longer watched: no read, no baseline kept
        assert (hub.marker(pid), reader.calls) == (None, 1)
    finally:
        await hub.stop()


async def test_change_right_after_open_emits() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.markers[pid] = (1, Decimal(10))
    hub = await started(reader)
    try:
        async with hub.subscribe(pid) as sub:
            await hub.prime(pid)
            reader.markers[pid] = (2, Decimal(21))  # before the hub's first poll
            assert await sub.next_event(1.0) is True
    finally:
        await hub.stop()


async def test_prime_failure_is_counted_not_raised() -> None:
    reader, pid = FakeReader(), uuid.uuid4()
    reader.failures = 1
    hub = await started(reader, interval=60)
    failures = REGISTRY.get_sample_value("spanlight_live_poll_failures_total") or 0.0
    try:
        async with hub.subscribe(pid):
            await hub.prime(pid)
            assert hub.marker(pid) is None
            assert REGISTRY.get_sample_value("spanlight_live_poll_failures_total") == failures + 1
    finally:
        await hub.stop()


@pytest.fixture
async def emptied(_empty_tables: Any) -> None:
    await _empty_tables()


@pytest.mark.usefixtures("emptied")
async def test_read_markers_after_ingest(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: Any
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id())]},
        headers=bearer(workspace.api_key),
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text
    pid, other = uuid.UUID(workspace.project_id), uuid.uuid4()

    async with session_factory() as db:
        markers = await read_markers(db, [pid, other])

    row_count, generation_sum = markers[pid]
    assert row_count > 0 and generation_sum > 0
    assert other not in markers
