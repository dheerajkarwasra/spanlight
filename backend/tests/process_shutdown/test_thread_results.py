"""Retained executor futures preserve values, failures and repeated caller cancellation."""

import asyncio
import threading

import pytest

from app.core.thread_ownership import owned_thread

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("cancellations", [0, 1, 3])
async def test_thread_outcome_after_repeated_cancellation(
    failure: bool, cancellations: int
) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished: list[bool] = []
    error = ValueError("test thread failure")

    def work() -> int:
        loop.call_soon_threadsafe(started.set)
        release.wait()
        finished.append(True)
        if failure:
            raise error
        return 42

    task = asyncio.create_task(owned_thread(asyncio.to_thread(work)))
    try:
        await asyncio.wait_for(started.wait(), 2)
        for index in range(cancellations):
            task.cancel(f"stop-{index}")
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        if cancellations:
            with pytest.raises(asyncio.CancelledError, match="stop-0"):
                await task
        elif failure:
            with pytest.raises(ValueError, match="test thread failure") as caught:
                await task
            assert caught.value is error
        else:
            assert await task == 42
        assert finished == [True]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
