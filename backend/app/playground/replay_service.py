"""Replay a stored LLM request through the gateway, under the project's monthly replay budget.

`run_replay` mirrors `app.playground.service.run` step for step, with a reservation in between
(ADR 0015, spec §6.5 to §6.6), and no transaction is open while the provider answers:

1. **Prepare**, in the request's project-bound transaction: the span and its trace's release,
   the credential (one of the organization's, of the same provider family), the request
   (`replay_source.classify`, `replay_request.build_replay`), the price book and the call's
   worst-case cost (the body's characters as input tokens, every allowed output token).
2. **Reserve**, in the same transaction under the project's advisory lock: the month's committed
   replay spend plus the estimate must fit `REPLAY_MONTHLY_BUDGET_USD`; the ledger row is
   inserted `running` holding the estimate, or `failed` with `budget_exceeded` and no provider
   request. Then the transaction is committed, which returns its connection to the pool.
3. **Call** through a route built in memory (`single_credential_context`): one credential, one
   attempt, no fallbacks, never streamed. The trace's environment is `playground`, its name
   `replay`, its tag `spanlight:replay`, its release the source trace's and its user the
   caller; the span carries the `spanlight.replay.*` attributes. The project's `block` budgets
   apply (`error = "BUDGET_EXCEEDED"`).
4. **Settle** (`app.playground.replay_settle`), in a session of its own and shielded from
   cancellation.

Request bodies and answers are never logged.
"""

import json
import time
import uuid
from datetime import datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import not_configured, not_found
from app.core.ids import new_id
from app.core.observability import REPLAY_RUNS
from app.db.models import Project, Span, Trace
from app.evals.budget import estimate_call_cost, month_start
from app.evals.reservation import BUDGET_EXCEEDED, MonthSpend
from app.gateway import service as gateway_service
from app.gateway.context import Clock, GatewayRequest, GatewayServices
from app.gateway.execute import execute as gateway_execute
from app.gateway.in_process import single_credential_context
from app.gateway.settle_helpers import may_have_billed, shielded
from app.gateway.trace_headers import TraceHeaders
from app.playground.output import result_of
from app.playground.replay_errors import REFUSALS, ReplayRefusedError
from app.playground.replay_queries import NewReplay, list_replays, month_spend, reserve_replay
from app.playground.replay_request import build_replay
from app.playground.replay_schemas import ReplayIn, ReplayOutcome, ReplaySourceView
from app.playground.replay_settle import TRACE_NAME, Prepared, ReplaySpans, Settlement
from app.playground.replay_source import Refusal, SpanFacts, classify, classify_edit
from app.playground.service import Execute, require_services
from app.pricing.cost import load_price_book

logger = structlog.get_logger(__name__)

ENVIRONMENT = "playground"
TAG = "spanlight:replay"
LAST_REPLAYS = 10

__all__ = [
    "ENVIRONMENT",
    "TAG",
    "TRACE_NAME",
    "ReplayBudgetExceededError",
    "read_source",
    "run_replay",
]


class ReplayBudgetExceededError(Exception):
    """The project's monthly replay budget cannot take this call; nothing was sent."""

    def __init__(self, spend: MonthSpend, budget: Decimal, month_start: datetime) -> None:
        super().__init__(BUDGET_EXCEEDED)
        self.spend = spend
        self.budget = budget
        self.month_start = month_start


def _facts(span: Span) -> SpanFacts:
    return SpanFacts(
        kind=span.kind,
        provider=span.provider,
        model=span.model,
        name=span.name,
        input=span.input,
        truncated=span.truncated,
        attributes=span.attributes,
    )


async def _source_span(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str
) -> tuple[Span, str | None] | None:
    """The span and its trace's release; None when the project has no such span."""
    row = (
        await db.execute(
            select(Span, Trace.release)
            .join(Trace, (Trace.project_id == Span.project_id) & (Trace.trace_id == Span.trace_id))
            .where(
                Span.project_id == project_id,
                Span.trace_id == trace_id.lower(),
                Span.span_id == span_id.lower(),
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def read_source(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str
) -> ReplaySourceView | None:
    """Classify a span without calling anything; None when it is not in the project."""
    found = await _source_span(db, project_id, trace_id, span_id)
    if found is None:
        return None
    span, _ = found
    replays = await list_replays(
        db, project_id, trace_id=span.trace_id, span_id=span.span_id, after=None, limit=LAST_REPLAYS
    )
    source = classify(_facts(span))
    if isinstance(source, Refusal):
        return ReplaySourceView(None, None, None, None, source, replays[:LAST_REPLAYS])
    return ReplaySourceView(
        source.fidelity, source.surface, source.family, source.body, None, replays[:LAST_REPLAYS]
    )


async def run_replay(
    db: AsyncSession,
    services: GatewayServices | None,
    project: Project,
    actor_id: uuid.UUID,
    body: ReplayIn,
    *,
    settings: Settings,
    clock: Clock,
    ip: str | None,
    execute: Execute = gateway_execute,
) -> ReplayOutcome:
    """Replay one stored request and answer with the result. Commits `db`.

    Errors: `409 NOT_CONFIGURED` without the gateway runtime, `CREDENTIALS_KEYS` or a replay
    budget; `404` for a span outside the project or a credential outside the organization;
    `ReplayRefusedError` (the `422 REPLAY_*` codes); `ReplayBudgetExceededError`.
    """
    services = require_services(services)
    if not settings.is_replay_configured:
        raise not_configured("Replay is turned off: REPLAY_MONTHLY_BUDGET_USD is 0.")
    now = clock()
    prepared = await _prepare(db, project, body, now)
    await _reserve(db, project, actor_id, body, prepared, settings, now)
    await db.commit()  # no connection is held while the provider answers
    return await _call(services, project, actor_id, prepared, clock, ip, execute)


async def _prepare(db: AsyncSession, project: Project, body: ReplayIn, now: datetime) -> Prepared:
    found = await _source_span(db, project.id, body.trace_id, body.span_id)
    if found is None:
        raise not_found()
    span, release = found
    credential = await gateway_service.org_credential(db, project.org_id, body.credential_id)
    if credential is None:
        raise not_found()
    facts = _facts(span)
    source = classify(facts) if body.edited_body is None else classify_edit(facts, body.edited_body)
    if isinstance(source, Refusal):
        raise ReplayRefusedError(source.code, source.detail)
    plan = build_replay(
        source,
        credential_provider=credential.provider,
        model_override=body.model,
        max_tokens_override=body.max_tokens,
        edited_body=body.edited_body,
    )
    prices = await load_price_book(db, project.org_id)
    estimate = estimate_call_cost(
        plan.model,
        json.dumps(plan.body, ensure_ascii=False),
        plan.max_tokens,
        prices,
        provider=credential.provider.value,
        at=now,
    )
    # A zero price (an organization's override) would make every replay free to the budget.
    if estimate is None or estimate <= 0:
        raise ReplayRefusedError("REPLAY_MODEL_UNPRICED", REFUSALS["REPLAY_MODEL_UNPRICED"])
    return Prepared(new_id(), span, release, credential, plan, prices, estimate)


async def _reserve(
    db: AsyncSession,
    project: Project,
    actor_id: uuid.UUID,
    body: ReplayIn,
    prepared: Prepared,
    settings: Settings,
    now: datetime,
) -> None:
    """Admit the replay under the budget (the row is inserted either way), or commit the refused
    row and raise with the month's figures, read under the same lock."""
    replay = NewReplay(
        id=prepared.replay_id,
        project_id=project.id,
        source_trace_id=prepared.span.trace_id,
        source_span_id=prepared.span.span_id,
        requested_by=actor_id,
        credential_id=body.credential_id,
        model=prepared.plan.model,
        fidelity=prepared.plan.fidelity,
        changed=list(prepared.plan.changed),
    )
    budget = settings.replay_monthly_budget_usd
    if await reserve_replay(db, replay, estimate=prepared.estimate, budget=budget, now=now):
        return
    spend = await month_spend(db, project.id, now)
    await db.commit()
    REPLAY_RUNS.labels(outcome=BUDGET_EXCEEDED).inc()
    raise ReplayBudgetExceededError(spend, budget, month_start(now))


async def _call(
    services: GatewayServices,
    project: Project,
    actor_id: uuid.UUID,
    prepared: Prepared,
    clock: Clock,
    ip: str | None,
    execute: Execute,
) -> ReplayOutcome:
    request = GatewayRequest(
        surface=prepared.plan.surface,
        body=prepared.plan.body,
        stream=False,
        trace=TraceHeaders(user_id=str(actor_id), tags=[TAG], release=prepared.release),
    )
    spans = ReplaySpans(prepared)
    context = single_credential_context(
        services,
        project,
        prepared.credential,
        environment=ENVIRONMENT,
        route_name=TRACE_NAME,
        span_sink=spans,
        clock=clock,
    )
    settle = Settlement(services, project, actor_id, prepared, spans, context, clock, ip)
    started = time.monotonic()
    try:
        result = await execute(request, context)
    except Exception as error:  # noqa: BLE001 - the provider may have been paid; charge the estimate
        logger.error("replay_call_failed", error_type=type(error).__name__)
        return await shielded(settle.unanswered())
    except BaseException:
        # Cancelled (the client went away); whether the provider was paid is unknown.
        await shielded(settle.unanswered())
        raise
    outcome = result_of(
        result,
        surface=request.surface,
        provider=prepared.credential.provider.value,
        model=prepared.plan.model,
        prices=prepared.prices,
        latency_ms=(time.monotonic() - started) * 1000,
        at=clock(),
    )
    return await shielded(settle.answered(outcome, billed=may_have_billed(result)))
