"""Retain executor work through cancellation; a cancelled await cannot stop its thread."""

import asyncio
from collections.abc import Awaitable


async def owned_thread[T](work: Awaitable[T]) -> T:
    """Shield from the first await, join, then preserve the caller's original cancellation.

    Resource acquisitions must retain the future themselves and close any acquired result
    when this raises cancellation. This helper neither retries effects nor shuts down executors.
    """
    future = asyncio.ensure_future(work)
    cancelled: asyncio.CancelledError | None = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            cancelled = cancelled or error
        except Exception:  # noqa: BLE001 - retrieve and re-raise the original outcome below
            break  # retrieve the original outcome below, after ownership has joined
    try:
        result = future.result()
    except BaseException as error:
        if cancelled is not None:
            raise cancelled from error
        raise
    if cancelled is not None:
        raise cancelled
    return result
