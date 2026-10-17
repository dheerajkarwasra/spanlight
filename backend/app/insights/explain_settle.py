"""The last step of an explanation: turn its budget reservation into what the call really cost.

Runs in a session of its own (not the request's, which may be torn down when the client goes
away) and under `shielded`, so cancelling the request does not cancel the settlement. The rules:

* an answer: the reservation becomes the explanation, with the answer and the cost of its usage;
* no answer, but the provider reported usage (it was paid): the row is completed without text
  and with that cost, so the spend stays in the budget; it is never shown; `502 EXPLAIN_FAILED`;
* no answer and no usage, but the request may have been generated (a timeout or connection
  error after it was sent, an answer too large to read, a success without usage): the row is
  completed without text at the reserved worst case; `502 EXPLAIN_FAILED`;
* no answer, no usage, and nothing was generated (the gateway refused the call before sending
  it, or the provider answered with an HTTP error status): the reservation is deleted;
  `502 EXPLAIN_FAILED`;
* the request was cancelled mid-call: whether the provider was paid is unknown, so the row is
  completed without text at the reserved worst case.

Every kept row gets an `insight.explain` audit event. Prompts and answers are never logged.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError, not_found
from app.db.models import AuditAction, InsightExplanation, ProviderCredential, ProviderKind
from app.db.rls import bind_project
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult, GatewayServices
from app.gateway.recorder import DirectSpans
from app.gateway.settle_helpers import may_have_billed
from app.insights import explain_queries
from app.insights.explain import answer_text
from app.insights.service import Actor
from app.pricing.cost import PriceBook
from app.services.audit import record_audit

logger = structlog.get_logger(__name__)

PROVIDER = ProviderKind.ANTHROPIC
MAX_TEXT_CHARS = 20_000  # the column's CHECK; 600 output tokens stay far below it


@dataclass(frozen=True)
class Prepared:
    """What the call needs, read before the reservation."""

    request: GatewayRequest
    model: str
    credential: ProviderCredential
    prices: PriceBook
    worst_case: Decimal


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    project_id: uuid.UUID
    insight_id: uuid.UUID
    actor: Actor
    worst_case: Decimal


async def settle(
    services: GatewayServices,
    reservation: Reservation,
    prepared: Prepared,
    result: GatewayResult,
    spans: DirectSpans,
    context: GatewayContext,
    clock: Callable[[], datetime],
) -> InsightExplanation:
    """Settle a call that returned (see the module docstring)."""
    await _write_spans(spans, context, reservation)
    answered = result.error is None and result.status == 200
    text = answer_text(result.body) if answered else None
    if text is None and not may_have_billed(result):
        await _release(services, reservation)
        _log_failure(reservation, result)
        raise _failed()
    cost = _real_cost(prepared, result, clock())
    row = await _complete(services, reservation, text, cost, clock())
    if text is None:
        _log_failure(reservation, result)
        raise _failed()
    return row


async def settle_abandoned(
    services: GatewayServices,
    reservation: Reservation,
    spans: DirectSpans,
    context: GatewayContext,
    clock: Callable[[], datetime],
) -> None:
    """Settle a call whose request was cancelled: the worst case stays charged."""
    await _write_spans(spans, context, reservation)
    await _complete(services, reservation, None, reservation.worst_case, clock())
    logger.warning(
        "insight_explain_abandoned",
        project_id=str(reservation.project_id),
        insight_id=str(reservation.insight_id),
    )


async def _complete(
    services: GatewayServices,
    reservation: Reservation,
    text: str | None,
    cost: Decimal,
    now: datetime,
) -> InsightExplanation:
    """Complete the reservation and audit it; `404` when the insight was deleted meanwhile."""
    async with services.sessions() as db, db.begin():
        await bind_project(db, reservation.project_id)
        row = await explain_queries.complete_reservation(
            db,
            reservation.id,
            text_=None if text is None else text[:MAX_TEXT_CHARS],
            cost_usd=cost,
            completed_at=now,
        )
        if row is None:  # the insight was deleted while the call ran, its reservation with it
            raise not_found()
        await _audit(db, reservation, row.model, cost)
    return row


async def _release(services: GatewayServices, reservation: Reservation) -> None:
    async with services.sessions() as db, db.begin():
        await bind_project(db, reservation.project_id)
        await explain_queries.delete_reservation(db, reservation.id)


async def _audit(db: AsyncSession, reservation: Reservation, model: str, cost: Decimal) -> None:
    actor = reservation.actor
    await record_audit(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=AuditAction.INSIGHT_EXPLAIN,
        target_type="insight",
        target_id=reservation.insight_id,
        ip=actor.ip,
        metadata={
            "insight_id": str(reservation.insight_id),
            "model": model,
            "cost_usd": str(cost),
        },
    )


async def _write_spans(
    spans: DirectSpans, context: GatewayContext, reservation: Reservation
) -> None:
    if not await spans.write(context):
        logger.warning("insight_explain_span_unrecorded", project_id=str(reservation.project_id))


def _real_cost(prepared: Prepared, result: GatewayResult, now: datetime) -> Decimal:
    """The call's cost from its usage; the reserved worst case when it cannot be priced."""
    usage = result.usage
    if usage is None:
        return prepared.worst_case
    priced = prepared.prices.cost(
        provider=PROVIDER.value,
        model=result.model or prepared.model,
        at=now,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
    )
    if priced is None or priced.cost_usd is None:
        return prepared.worst_case
    return priced.cost_usd


def _log_failure(reservation: Reservation, result: GatewayResult) -> None:
    logger.warning(
        "insight_explain_failed",
        project_id=str(reservation.project_id),
        insight_id=str(reservation.insight_id),
        status=result.status,
        code=result.error.spanlight_code if result.error is not None else None,
        usage_reported=result.usage is not None,
    )


def _failed() -> ProblemError:
    return ProblemError(502, "EXPLAIN_FAILED", "Claude could not explain this insight.")
