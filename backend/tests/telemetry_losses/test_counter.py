"""The in-process loss counter: capped, drained by swap, restorable, and safe across threads."""

import threading
import uuid
from datetime import UTC, datetime

from app.telemetry_losses.counter import LossCounter

HOUR = datetime(2026, 10, 16, 9, tzinfo=UTC)


def test_add_accumulates_per_key() -> None:
    counter = LossCounter()
    project = uuid.uuid4()

    counter.add(project, "gateway", "overflow", HOUR)
    counter.add(project, "gateway", "overflow", HOUR, n=4)
    counter.add(project, "gateway", "error", HOUR)

    assert counter.drain() == {
        (project, "gateway", "overflow", HOUR): 5,
        (project, "gateway", "error", HOUR): 1,
    }


def test_add_ignores_a_count_below_one() -> None:
    counter = LossCounter()

    counter.add(uuid.uuid4(), "gateway", "error", HOUR, n=0)
    counter.add(uuid.uuid4(), "gateway", "error", HOUR, n=-3)

    assert counter.drain() == {}


def test_counter_caps_at_10000_keys_and_collapses_into_other() -> None:
    counter = LossCounter()
    project = uuid.uuid4()
    assert LossCounter.MAX_KEYS == 10_000
    for index in range(LossCounter.MAX_KEYS):
        counter.add(uuid.uuid4(), "ingest", f"reason-{index}", HOUR)

    counter.add(project, "ingest", "validation", HOUR, n=2)
    counter.add(project, "ingest", "usage", HOUR, n=3)

    entries = counter.drain()
    assert (project, "ingest", "validation", HOUR) not in entries
    assert (project, "ingest", "usage", HOUR) not in entries
    assert entries[(project, "ingest", "other", HOUR)] == 5
    assert len(entries) == LossCounter.MAX_KEYS + 1


def test_an_existing_key_still_counts_past_the_cap() -> None:
    counter = LossCounter()
    project = uuid.uuid4()
    counter.add(project, "gateway", "overflow", HOUR)
    for index in range(LossCounter.MAX_KEYS - 1):
        counter.add(uuid.uuid4(), "ingest", f"reason-{index}", HOUR)

    counter.add(project, "gateway", "overflow", HOUR)

    assert counter.drain()[(project, "gateway", "overflow", HOUR)] == 2


def test_drain_returns_and_empties() -> None:
    counter = LossCounter()
    project = uuid.uuid4()
    counter.add(project, "sdk", "dropped", HOUR, n=7)

    assert counter.drain() == {(project, "sdk", "dropped", HOUR): 7}
    assert counter.drain() == {}


def test_restore_adds_to_live_counts() -> None:
    counter = LossCounter()
    project = uuid.uuid4()
    counter.add(project, "gateway", "error", HOUR, n=2)
    drained = counter.drain()
    counter.add(project, "gateway", "error", HOUR, n=3)
    counter.add(project, "gateway", "rejected", HOUR)

    counter.restore(drained)

    assert counter.drain() == {
        (project, "gateway", "error", HOUR): 5,
        (project, "gateway", "rejected", HOUR): 1,
    }


def test_concurrent_adds_lose_nothing() -> None:
    counter = LossCounter()
    project = uuid.uuid4()

    def work() -> None:
        for _ in range(2_000):
            counter.add(project, "gateway", "overflow", HOUR)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.drain() == {(project, "gateway", "overflow", HOUR): 16_000}
