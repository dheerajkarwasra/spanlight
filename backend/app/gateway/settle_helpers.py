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
    """Wait up to `DRAIN_TIMEOUT_SECONDS` for settlements still running; for the app's shutdown,
    before the database engine is disposed. One that does not finish in time stays a
    reservation, which the cleanup job or the reaper later charges at its worst-case cost."""
    pending = set(_settling)
    if not pending:
        return
    _, unfinished = await asyncio.wait(pending, timeout=DRAIN_TIMEOUT_SECONDS)
    if unfinished:
        logger.warning("insight_explain_settle_unfinished", settlements=len(unfinished))


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
