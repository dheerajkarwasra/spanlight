"""Join gateway database users without cancelling transaction cleanup twice."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


async def retained[T](work: Coroutine[Any, Any, T]) -> T:
    """Keep the cleanup owned until it returns, including repeated waiter cancellation."""
    task = asyncio.create_task(work)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def join(
    snapshot: Callable[[], set[asyncio.Task[Any]]],
    *,
    cancel_at: float | None,
    deadline: float | None,
) -> bool:
    """Cancel once at the work boundary, then join up to an optional absolute deadline.

    None means cooperative completion, not a hard process bound. Newly enqueued cleanup
    participates until stable. A false result never permits disposal of its database pool.
    """
    loop = asyncio.get_running_loop()
    cancelled: set[asyncio.Task[Any]] = set()
    while True:
        pending = {task for task in snapshot() if not task.done()}
        if not pending:
            await asyncio.sleep(0)  # completion callbacks may enqueue more cleanup
            if not any(not task.done() for task in snapshot()):
                return True
            continue
        now = loop.time()
        if cancel_at is not None and now >= cancel_at:
            for task in pending - cancelled:
                if not task.cancelling():
                    task.cancel()
            cancelled.update(pending)
        if deadline is not None and now >= deadline:
            return False
        boundary = deadline if cancel_at is None or now >= cancel_at else cancel_at
        if deadline is not None and boundary is not None:
            boundary = min(boundary, deadline)
        await asyncio.wait(pending, timeout=None if boundary is None else max(0, boundary - now))
