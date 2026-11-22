"""Explain with AI: one insight explained through the gateway, under a monthly budget.

`explain` runs in four steps, and no transaction is open while the provider answers:

1. **Prepare**, in the request's project-bound transaction: the insight, the organization's
   oldest credential for the configured provider, the price book, the trace excerpts, the
   request (`app.insights.explain`) and the call's cost ceiling. The transaction is committed, which
   returns its connection to the pool.
2. **Reserve**, in one short transaction bound to the project and under an advisory lock per
   organization: the organization's month-to-date explanation cost (reservations included,
   summed by a database function so the request never bypasses row-level security) plus the
   worst case must not exceed `EXPLAIN_MONTHLY_BUDGET_USD`; a reservation row holding the worst
   case is inserted and committed. Two concurrent requests cannot both pass the check on the
   same sum.
3. **Call** the gateway in process (`key=None`: no cache, faults or key limits) through a route
   built in memory: the one credential, the configured model, one attempt, no fallbacks. The
   trace's environment is `doctor` and its tag `doctor-explain`; the span is written into the
   project like any other call (it is real spend there) and awaited (`DirectSpans`).
4. **Settle** (`app.insights.explain_settle`), shielded from cancellation and in a session of
   its own, so a client that goes away mid-call still leaves a settled row. An answer is stored
   with the cost of its usage. A failure keeps its row, without text and never shown, whenever
   the provider may have billed it: at the reported usage's cost, or at the worst case after a
   timeout, a connection error or a cancellation once the request was sent. Only a call the
   gateway never sent, or one the provider refused with an HTTP error status (no usage),
   releases its reservation.

Prompts and answers are never logged.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.gemini import reserve_gemini
from app.budgets.provider_bounds import provider_cost_ceiling
from app.core.errors import ProblemError, not_configured, not_found
from app.db.models import InsightExplanation, Project, ProviderKind
from app.db.rls import bind_project
from app.gateway.context import GatewayRequest, GatewayServices
from app.gateway.execute import execute
from app.gateway.provider_billing import NEW_TEXT_PROVIDERS
from app.gateway.recorder import DirectSpans
from app.gateway.settle_helpers import shielded
from app.insights import explain_queries, queries
from app.insights.catalogue import KIND_INFO
from app.insights.explain import (
    MAX_EXCERPTS,
    MAX_OUTPUT_TOKENS,
    build_explain_request,
    worst_case_input_tokens,
)
from app.insights.explain_context import explain_context
from app.insights.explain_settle import (
    Prepared,
    Reservation,
    settle,
    settle_abandoned,
)
from app.insights.service import Actor
from app.pricing.cost import PriceBook, load_price_book


async def explain(
    db: AsyncSession,
    services: GatewayServices | None,
    project: Project,
    insight_id: uuid.UUID,
    actor: Actor,
    *,
    clock: Callable[[], datetime],
) -> InsightExplanation:
    """Explain one insight of `project` and store the answer. Commits `db` (more than once).

    Errors: `404` for an insight that is not the project's; `409 NOT_CONFIGURED` without the
    gateway runtime, a budget, `CREDENTIALS_KEYS` or a matching credential; `409
    EXPLAIN_MODEL_UNPRICED`; `402 EXPLAIN_BUDGET_EXCEEDED`; `502 EXPLAIN_FAILED`.
    """
    now = clock()
    prepared, services = await _prepare(db, services, project, insight_id, now)
    await db.commit()  # no connection is held while the provider answers
    reservation = await _reserve(db, services, project, insight_id, actor, prepared, now)
    spans = DirectSpans()
    context = explain_context(services, project, prepared.credential, span_sink=spans, clock=clock)
    try:
        result = await execute(prepared.request, context)
    except BaseException:
        # `execute` raises only when cancelled (the client went away). Whether the provider was
        # paid is unknown, so the worst case stays charged.
        await shielded(settle_abandoned(services, reservation, spans, context, clock))
        raise
    return await shielded(settle(services, reservation, prepared, result, spans, context, clock))


async def _prepare(
    db: AsyncSession,
    services: GatewayServices | None,
    project: Project,
    insight_id: uuid.UUID,
    now: datetime,
) -> tuple[Prepared, GatewayServices]:
    insight = await queries.get_insight(db, project.id, insight_id)
    if insight is None:
        raise not_found()
    services = _configured(services)
    provider = services.settings.explain_provider
    credential = await explain_queries.oldest_credential(db, project.org_id, provider)
    if credential is None:
        raise not_configured(f"Add a provider credential for EXPLAIN_PROVIDER={provider}.")
    model = services.settings.explain_model
    prices = await load_price_book(db, project.org_id)
    trace_ids = [str(trace_id) for trace_id in insight.evidence.get("trace_ids", [])]
    excerpts = await explain_queries.trace_excerpts(db, project.id, trace_ids[:MAX_EXCERPTS])
    request = build_explain_request(
        insight,
        KIND_INFO.get(insight.kind),
        excerpts,
        model=model,
        provider=provider,
    )
    worst = _estimate(provider, model, request, prices, now)
    # A zero price (an organization's override, say) would make every call free to the budget.
    if worst is None or worst <= 0:
        raise ProblemError(
            409,
            "EXPLAIN_MODEL_UNPRICED",
            f"EXPLAIN_MODEL {model} has no verified cost bound for {provider}.",
        )
    return Prepared(request, model, credential, prices, worst), services


def _estimate(
    provider: ProviderKind, model: str, request: GatewayRequest, prices: PriceBook, now: datetime
) -> Decimal | None:
    if provider == ProviderKind.GEMINI:
        bound = reserve_gemini(
            request.body, [model], prices, at=now, fallback_cap=MAX_OUTPUT_TOKENS
        )
        return bound.usd if bound is not None else None
    if provider in NEW_TEXT_PROVIDERS:
        # DeepSeek has an admission-only bound. Doctor requires a price under the selected provider.
        if prices.find(provider, model, now) is None:
            return None
        return provider_cost_ceiling(provider, model, request.body, prices, now)
    priced = prices.cost(
        provider=provider,
        model=model,
        at=now,
        input_tokens=worst_case_input_tokens(request),
        output_tokens=MAX_OUTPUT_TOKENS,
        cached_tokens=None,
    )
    return priced.cost_usd if priced is not None else None


def _configured(services: GatewayServices | None) -> GatewayServices:
    """The services, when the gateway runs here and explanations are switched on."""
    if services is None:
        raise not_configured("The gateway runtime is not running in this process.")
    settings = services.settings
    if settings.explain_monthly_budget_usd <= 0:
        raise not_configured("Explanations are off: EXPLAIN_MONTHLY_BUDGET_USD is 0.")
    if not settings.is_crypto_configured:
        raise not_configured("Set CREDENTIALS_KEYS so provider credentials can be used.")
    return services


async def _reserve(
    db: AsyncSession,
    services: GatewayServices,
    project: Project,
    insight_id: uuid.UUID,
    actor: Actor,
    prepared: Prepared,
    now: datetime,
) -> Reservation:
    """Check the budget and reserve the worst case, in one committed transaction.

    The lock, the sum and the insert share that transaction, so two concurrent requests cannot
    both pass the check on the same sum. The transaction is bound to `project` (the request's
    first one ended with the prepare step's commit), so the insert is checked by row-level
    security; the organization-wide sum is a database function that needs no bypass.
    """
    budget = services.settings.explain_monthly_budget_usd
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    await bind_project(db, project.id)
    await explain_queries.lock_org_budget(db, project.org_id)
    spent = await explain_queries.month_to_date_cost(db, project.org_id, month_start)
    if spent + prepared.worst_case > budget:
        await db.rollback()
        raise ProblemError(
            402,
            "EXPLAIN_BUDGET_EXCEEDED",
            f"The organization's explain budget of ${budget} for this month is used up.",
        )
    reservation_id = await explain_queries.insert_reservation(
        db,
        project_id=project.id,
        insight_id=insight_id,
        model=prepared.model,
        cost_usd=prepared.worst_case,
        created_by=actor.user_id,
        created_at=now,
    )
    await db.commit()
    return Reservation(reservation_id, project.id, insight_id, actor, prepared.worst_case)
