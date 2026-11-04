"""Helpers for settling a provider call that may have been paid for, shared by every caller that
spends through the gateway in process (insight explanations, the playground, LLM judges and
experiments).

`shielded` runs a settlement to its end even when the awaiting request or task is cancelled,
and keeps a reference so the app's shutdown can drain it (`drain_settling`). `may_have_billed`
is the one rule for "could the provider have charged for this call".
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

from app.core.errors import ProblemError
from app.gateway.cleanup_join import join, retained
from app.gateway.context import GatewayResult

logger = structlog.get_logger(__name__)

# Settlements still running after their caller was cancelled. Holding a reference keeps the
# task from being collected before it finishes; the app's shutdown drains them.
_settling: set["asyncio.Task[Any]"] = set()

DRAIN_TIMEOUT_SECONDS = 10.0


async def shielded[T](work: Coroutine[Any, Any, T]) -> T:
    """Run `work` to the end even when the awaiting request is cancelled."""
    task = asyncio.ensure_future(work)
    _settling.add(task)
    task.add_done_callback(_settled)
    return await asyncio.shield(task)


def _settled(task: "asyncio.Task[Any]") -> None:
    """Forget a finished settlement and log a failure nobody may be awaiting any more.

    The caller that started it may have been cancelled, so its exception would otherwise
    surface only as asyncio's unstructured "never retrieved" message. Expected outcomes that
    callers turn into responses are `ProblemError`s and are not logged here.
    """
    _settling.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None and not isinstance(error, ProblemError):
        logger.error("insight_explain_settle_failed", error_type=type(error).__name__)


async def drain_settling() -> None:
    """Drain settlements, then cancel and join them while the database stays usable.

    Driver cancellation remains cooperative; this legacy callback has no process bound.
    """
    await retained(
        join_settling(cancel_at=asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS)
    )


def pending_settlements() -> tuple[asyncio.Task[Any], ...]:
    """Retained settlement database users for the process owner's final join barrier."""
    return tuple(task for task in _settling if not task.done())


async def join_settling(*, cancel_at: float, deadline: float | None = None) -> bool:
    """Join retained settlements; false means a process owner must keep pools alive."""
    joined = await join(lambda: set(_settling), cancel_at=cancel_at, deadline=deadline)
    if not joined:
        logger.warning(
            "process.shutdown_incomplete",
            phase="finalizers",
            reason="join_timeout",
            accounting_complete=False,
        )
    return joined


def may_have_billed(result: GatewayResult) -> bool:
    """Whether the provider may have generated (and billed) anything for the call.

    True when it reported usage, or when any attempt was sent and did not end in an HTTP error
    status (a timeout, a connection error, an unreadable or usage-less answer). False only when
    nothing was sent (no attempt, or a `blocked` one) or every attempt got a 4xx/5xx status.
    """
    if result.usage is not None:
        return True
    return any(
        attempt.error != "blocked" and (attempt.status is None or attempt.status < 400)
        for attempt in result.attempts
    )
