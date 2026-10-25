"""Reserve a gateway call's estimated cost against its `block` budgets, then settle the real cost.

The guard runs `reserve` in its short transaction before the upstream call (ADR 0017, the judge
pattern of ADR 0015 with a counter row instead of a per-call sum): one `budget_counters` row per
(budget, period) holds the gateway spend settled this period (G) and the reservations in flight
(R). With M the spend the alert evaluator last measured this period and E this call's estimate,
the call is admitted when `max(M, G) + R + E <= amount` for every matching budget; `max(M, G)`
carries a budget created or edited mid-period with no backfill. The counter rows are locked
`FOR UPDATE` in `budget_id` order (no deadlock when one call matches several budgets) and every
check runs before any write, so a refusal writes nothing. The caller commits before forwarding,
so no connection is held while the provider answers and the next call sees this reservation.

`settle` runs in the span's transaction (`app.gateway.span_write`): it deletes the call's
reservation rows and replaces each amount with the stored span's cost, or with
`app.evals.budget.failed_call_charge` when the cost is unknown (nothing for a call never sent
or refused with an HTTP error status, else the estimate). A retry that finds no row writes
nothing. A row the reaper (`app.budgets.jobs`) already counted at its estimate is corrected
by `charge - amount`, so the real cost wins.

The estimate is pure: the text of the request (`messages`, `input`, `system`, `instructions`,
`tools`; image, file and audio parts are not counted) by `app.pricing.tokens`, and the
requested `max_tokens` (or `OUTPUT_TOKENS_FALLBACK` without one) times `n` as output, at the
dearest price among the requested model and the route's targets for the requested
`service_tier`. No priced candidate gives None and the call is admitted as unknown, as ADR
0012 does; a zero estimate (a $0 price override) reserves nothing either.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from app.core.ids import new_id
from app.db.models import BudgetCounter, BudgetReservation, Span
from app.db.rls import bind_project
from app.evals.budget import failed_call_charge
from app.gateway.upstream_io import STREAM_TOTAL_TIMEOUT_S
from app.pricing.cost import MAX_PRICED_TOKENS, PriceBook
from app.pricing.tiers import normalize_tier
from app.pricing.tokens import estimate_text_tokens

logger = structlog.get_logger(__name__)

# Output tokens reserved when the request sets no max_tokens (LiteLLM's fallback); the owner
# may change it. A lower max_tokens on the request lowers the reservation.
OUTPUT_TOKENS_FALLBACK = 16_384
# How long a reservation is held before the reaper counts it at its estimate: the longest a
# stream may run after its first chunk, plus the route's time to that chunk, the recorder's
# retries and clock skew.
RESERVATION_LEASE = timedelta(seconds=STREAM_TOTAL_TIMEOUT_S) + timedelta(minutes=2)
HELD, REAPED = "held", "reaped"

_INPUT_KEYS = ("messages", "input", "system", "instructions", "tools")
_OUTPUT_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
# Content parts that carry no text: images, files and audio are not counted (settle charges
# the real cost).
_SKIPPED_PARTS = frozenset(
    {"image", "image_url", "input_image", "input_file", "file", "document", "input_audio", "audio"}
)


@dataclass(frozen=True, slots=True)
class BlockBudget:
    """A matching block budget as the guard read it; `measured_usd` is M (zero when unknown)."""

    id: uuid.UUID
    name: str
    period: BudgetPeriod
    amount_usd: Decimal
    measured_usd: Decimal


@dataclass(frozen=True, slots=True)
class Refusal:
    """The budget the call did not fit, what is committed against it and the call's estimate."""

    budget: BlockBudget
    committed_usd: Decimal
    estimate_usd: Decimal


def input_text(body: Mapping[str, Any]) -> str:
    """Every string in the request's text-bearing fields, image, file and audio parts left out."""
    texts: list[str] = []
    _collect(tuple(body.get(key) for key in _INPUT_KEYS), texts)
    return "".join(texts)


def _collect(value: Any, texts: list[str]) -> None:
    if isinstance(value, str):
        texts.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, texts)
    elif isinstance(value, Mapping) and value.get("type") not in _SKIPPED_PARTS:
        for item in value.values():
            _collect(item, texts)


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def output_tokens(body: Mapping[str, Any]) -> int:
    """The most output tokens the call may produce: the requested cap (or the fallback) times
    `n`."""
    caps = [cap for cap in map(_positive_int, (body.get(key) for key in _OUTPUT_KEYS)) if cap]
    return (max(caps) if caps else OUTPUT_TOKENS_FALLBACK) * (_positive_int(body.get("n")) or 1)


def estimate(
    body: Mapping[str, Any],
    candidates: Sequence[tuple[str | None, str]],
    prices: PriceBook,
    *,
    at: datetime,
) -> Decimal | None:
    """The most the call can cost: the dearest of the `(provider, model)` candidates the book
    prices (the model asked for, then each target's), at the requested tier; None when none is.
    The provider picks the tier's factor (priority, flex, batch); None prices any provider."""
    # Clamped: a count the book cannot price would admit the call as unknown, so an absurd
    # `max_tokens` or `n` must price as a huge estimate (and be refused) instead.
    tokens = min(estimate_text_tokens(input_text(body)), MAX_PRICED_TOKENS)
    output = min(output_tokens(body), MAX_PRICED_TOKENS)
    raw_tier = body.get("service_tier")
    tier_name = raw_tier if isinstance(raw_tier, str) else None
    costs = [
        cost.cost_usd
        for provider, model in candidates
        if (
            cost := prices.cost(
                provider=provider,
                model=model,
                at=at,
                input_tokens=tokens,
                output_tokens=output,
                cached_tokens=None,
                service_tier=normalize_tier(provider, tier_name),
            )
        )
        is not None
        and cost.cost_usd is not None
    ]
    return max(costs) if costs else None


async def reserve(
    db: AsyncSession,
    project_id: uuid.UUID,
    budgets: Sequence[BlockBudget],
    estimate_usd: Decimal,
    *,
    trace_id: str,
    span_id: str,
    now: datetime,
) -> Refusal | None:
    """Hold `estimate_usd` against every budget, or refuse on the first (lowest amount) that it
    does not fit. A refusal writes only the counter rows that were missing (at zero), never a
    reservation. Runs in the caller's transaction under the project binding; the caller holds
    `FOR KEY SHARE` on the budgets, so none is edited or deleted meanwhile."""
    periods = {budget.id: period_start(budget.period, now) for budget in budgets}
    keys = sorted(periods.items())
    await db.execute(
        insert(BudgetCounter)
        .values(
            [
                {"budget_id": budget_id, "period_start": started, "project_id": project_id}
                for budget_id, started in keys
            ]
        )
        .on_conflict_do_nothing()
    )
    rows = await db.execute(
        select(BudgetCounter.budget_id, BudgetCounter.settled_usd, BudgetCounter.reserved_usd)
        .where(tuple_(BudgetCounter.budget_id, BudgetCounter.period_start).in_(keys))
        .order_by(BudgetCounter.budget_id)
        .with_for_update()
    )
    counters = {row.budget_id: row for row in rows}
    budgets = [budget for budget in budgets if budget.id in counters]  # none gone, in practice
    for budget in budgets:
        counter = counters[budget.id]
        committed = max(budget.measured_usd, counter.settled_usd) + counter.reserved_usd
        if committed + estimate_usd > budget.amount_usd:
            return Refusal(budget, committed, estimate_usd)
    await db.execute(
        update(BudgetCounter)
        .where(tuple_(BudgetCounter.budget_id, BudgetCounter.period_start).in_(keys))
        .values(reserved_usd=BudgetCounter.reserved_usd + estimate_usd, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        insert(BudgetReservation).values(
            [
                {
                    "id": new_id(),
                    "project_id": project_id,
                    "budget_id": budget.id,
                    "period_start": periods[budget.id],
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "amount_usd": estimate_usd,
                    "status": HELD,
                    "expires_at": now + RESERVATION_LEASE,
                }
                for budget in budgets
            ]
        )
    )
    return None


async def settle(
    db: AsyncSession, project_id: uuid.UUID, span: Mapping[str, Any], now: datetime
) -> None:
    """Replace the call's reservations with what its stored span cost. Runs after the span is
    written, in its transaction; a call that reserved nothing (or settled already) writes nothing.
    """
    rows = (
        await db.execute(
            delete(BudgetReservation)
            .where(
                BudgetReservation.project_id == project_id,
                BudgetReservation.trace_id == span.get("trace_id"),
                BudgetReservation.span_id == span.get("span_id"),
            )
            .returning(
                BudgetReservation.budget_id,
                BudgetReservation.period_start,
                BudgetReservation.amount_usd,
                BudgetReservation.status,
            )
            .execution_options(synchronize_session=False)
        )
    ).all()
    if not rows:
        return
    cost = await db.scalar(
        select(Span.cost_usd).where(
            Span.project_id == project_id,
            Span.trace_id == span.get("trace_id"),
            Span.span_id == span.get("span_id"),
        )
    )
    for row in sorted(rows, key=lambda row: row.budget_id):
        amount = Decimal(row.amount_usd)
        charge = (
            Decimal(cost)
            if cost is not None
            else failed_call_charge(
                may_have_billed=_may_have_billed(span), usage_cost=None, estimate=amount
            )
        )
        # A reaped row was counted at its estimate already: only the difference is added.
        held = row.status == HELD
        released, counted = (amount, charge) if held else (Decimal(0), charge - amount)
        await db.execute(
            update(BudgetCounter)
            .where(
                BudgetCounter.budget_id == row.budget_id,
                BudgetCounter.period_start == row.period_start,
            )
            .values(
                reserved_usd=func.greatest(BudgetCounter.reserved_usd - released, 0),
                settled_usd=func.greatest(BudgetCounter.settled_usd + counted, 0),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )


def _may_have_billed(span: Mapping[str, Any]) -> bool:
    """Whether the provider may have billed a call whose cost is unknown: it was sent at least
    once and the last answer was not an HTTP error status (a timeout, a cut stream or a 200
    without usage may have been)."""
    attributes = span.get("attributes") or {}
    if not attributes.get("spanlight.gateway.attempts"):
        return False
    status = attributes.get("spanlight.gateway.upstream_status")
    return not (isinstance(status, int) and status >= 400)


async def settle_unstored(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID, span: Mapping[str, Any]
) -> None:
    """Settle the reservations of a span the recorder gave up on, in a short transaction of its
    own, so the budget stops holding the estimate now rather than at the lease's end. The span
    is not stored, so each is charged by `failed_call_charge`. Best effort: a failure is logged
    and the reaper finishes the job."""
    try:
        async with sessions() as db, db.begin():
            await bind_project(db, project_id)
            await settle(db, project_id, span, datetime.now(UTC))
    except Exception as failure:  # noqa: BLE001 - the reaper covers it
        logger.warning(
            "budget.settle_unstored_failed",
            span_id=span.get("span_id"),
            error_type=type(failure).__name__,
        )
