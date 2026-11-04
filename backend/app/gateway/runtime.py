"""The gateway's process-wide services, built once per app lifespan.

`GatewayRuntime` holds what every gateway call shares: the one upstream HTTP client (see
`app.gateway.http`), the budget guard, the rate limiter on its own pool, the response cache,
the request session factory, the settings and the span recorder. It satisfies
`app.gateway.context.GatewayServices`, so `context_for_key` and `context_for_project` take it
as it is.

The api builds one in its lifespan whatever `GATEWAY_MODE` says (credential checks and
in-process callers use the client even when `/gw/*` is served elsewhere); the standalone
gateway app builds its own. `close_runtime` is the shutdown order: the spans still being
written are drained first, while the database engine is still open, then the client is closed.
The caller disposes the engines after it.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.guard import get_budget_guard
from app.config import Settings
from app.gateway import recorder
from app.gateway.budget import BudgetGuard
from app.gateway.cache import GatewayCache, PostgresGatewayCache
from app.gateway.key_limits import GatewayLimiter
from app.gateway.settle_helpers import join_settling

# Ordinary work allowance; cancellation cleanup is joined afterward while pools stay open.
DRAIN_TIMEOUT_S = 10.0


class SpanRecorder(Protocol):
    """Where a call's span goes after the answer; shutdown waits for it."""

    async def drain(self, timeout_s: float | None = None) -> None: ...

    async def shutdown(self, *, cancel_at: float, deadline: float) -> recorder.RecorderShutdown: ...


class BackgroundRecorder:
    """The recorder of `app.gateway.recorder`: spans written by bounded background tasks."""

    async def drain(self, timeout_s: float | None = None) -> None:
        await recorder.drain(timeout_s=timeout_s)

    async def shutdown(self, *, cancel_at: float, deadline: float) -> recorder.RecorderShutdown:
        return await recorder.shutdown(cancel_at=cancel_at, deadline=deadline)


@dataclass(frozen=True)
class GatewayRuntime:
    http: httpx.AsyncClient
    budget_guard: BudgetGuard
    limiter: GatewayLimiter
    cache: GatewayCache
    sessions: async_sessionmaker[AsyncSession]
    settings: Settings
    recorder: SpanRecorder


def build_runtime(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    http: httpx.AsyncClient,
    rate_limit_sessions: async_sessionmaker[AsyncSession],
) -> GatewayRuntime:
    """The runtime over an already built client and session factories.

    `rate_limit_sessions` is the rate-limit pool (`app.core.ratelimit`), so a burst of limited
    calls cannot take the connections the calls themselves need. The guard reads the evaluated
    state of the project's `block` budgets (`app.budgets.guard`).
    """
    return GatewayRuntime(
        http=http,
        budget_guard=get_budget_guard(settings),
        limiter=GatewayLimiter(rate_limit_sessions),
        cache=PostgresGatewayCache(),
        sessions=sessions,
        settings=settings,
        recorder=BackgroundRecorder(),
    )


async def close_runtime(
    runtime: GatewayRuntime, *, drain_timeout_s: float = DRAIN_TIMEOUT_S
) -> None:
    """Write the spans still queued, then close the upstream client. Engines stay open."""
    await runtime.recorder.drain(timeout_s=drain_timeout_s)
    await runtime.http.aclose()


async def join_runtime(
    runtime: GatewayRuntime, *, cancel_at: float, deadline: float
) -> recorder.RecorderShutdown:
    """Join internal producers after the server/worker has joined external producers.

    Task15D owns that prerequisite and the absolute deadlines. A failed join forbids final
    loss flushing and pool disposal. Joined uncertain writes permit known-loss flushing,
    but accounting_complete remains false. This function does not unwind resources.
    """
    if not await join_settling(cancel_at=cancel_at, deadline=deadline):
        return recorder.RecorderShutdown(joined=False, accounting_complete=False)
    return await runtime.recorder.shutdown(cancel_at=cancel_at, deadline=deadline)
