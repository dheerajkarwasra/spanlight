"""Budget guard protocol: decide before any upstream call whether a request may proceed.

Pure protocol and value types. ``execute`` calls the guard once per request, after the model
is resolved and before the cache and the attempt loop. A blocked decision becomes a
``BUDGET_EXCEEDED`` error with zero attempts and no upstream call. ``execute`` records
``spanlight.budget.blocked=true`` and ``spanlight.budget.id`` on the error span. The real
implementation is ``app.budgets.guard.StateBudgetGuard``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.errors import GatewayError, budget_exceeded


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of one budget check. ``reason`` is shown to the client when blocked.

    ``reason`` must not end with a period: ``budget_exceeded`` appends one.
    """

    allowed: bool
    budget_id: UUID | None = None
    reason: str | None = None


class BudgetGuard(Protocol):
    """Decides whether one gateway request may reach a provider.

    ``external_user_id`` is the request's end-user id (``x-spanlight-user``, or the trace user
    id), None when absent. Phase 3 enforces ``scope=user`` budgets by it. ``model`` is the model
    the client asked for; ``target_models`` are the names the route's targets would send upstream
    (after their aliases), so a ``scope=model`` budget can match either. ``metadata`` and
    ``trace_name`` are the call's trace dimensions as the gateway knows them (the headers,
    merged with the stored trace when the call joins one); None for in-process callers, which
    keep the project, model and user check only. ``body`` is the request body the call's cost
    is estimated from, ``trace_id`` and ``span_id`` name the span the reservation settles with,
    and ``org_id`` picks the organization's price overrides; without them the guard only reads
    the evaluated state and reserves nothing. ``target_providers`` are the providers of
    ``target_models``, in the same order, for the service tier's price factor.
    """

    async def check(
        self,
        db: AsyncSession,
        project_id: UUID,
        key_id: UUID | None,
        model: str,
        external_user_id: str | None,
        now: datetime,
        target_models: Sequence[str] = (),
        *,
        metadata: Mapping[str, str] | None = None,
        trace_name: str | None = None,
        target_providers: Sequence[str | None] = (),
        body: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        org_id: UUID | None = None,
    ) -> BudgetDecision: ...


def blocked_error(decision: BudgetDecision) -> GatewayError:
    """Render a blocked decision as the provider-shaped 402 error."""
    return budget_exceeded(decision.reason or "the request exceeds a configured limit")
