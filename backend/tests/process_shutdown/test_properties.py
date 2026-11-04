"""Bounded deadline and cooperative join schedules exercise observable ownership."""

import asyncio

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from app.process_shutdown import ProcessShutdown, ShutdownBudget, wait_owned

pytestmark = pytest.mark.serial


@hypothesis_settings(max_examples=16, deadline=None, derandomize=True)
@given(signals=st.integers(1, 5), cancellations=st.integers(0, 3), tasks=st.integers(1, 3))
async def test_repeated_stops_and_waiter_cancellation_preserve_deadline_and_join(
    signals: int,
    cancellations: int,
    tasks: int,
) -> None:
    process = ProcessShutdown(ShutdownBudget(1, 0.1, 0.1, 0.1))
    process.begin()
    original_deadline = process.deadline
    release = asyncio.Event()
    completed: list[int] = []

    async def producer(index: int) -> None:
        await release.wait()
        completed.append(index)

    owned = {asyncio.create_task(producer(index)) for index in range(tasks)}
    joiner = asyncio.create_task(process.join_producers(lambda: owned))
    waiter = asyncio.create_task(wait_owned(joiner))
    await asyncio.sleep(0)
    for _ in range(signals):
        process.begin()
        await asyncio.sleep(0)
    for _ in range(cancellations):
        waiter.cancel()
        await asyncio.sleep(0)
    assert process.deadline == original_deadline
    assert not joiner.done()
    assert completed == []
    release.set()
    await asyncio.gather(waiter, return_exceptions=True)
    assert sorted(completed) == list(range(tasks))
    assert all(task.done() for task in owned)
    assert process.producers_joined
