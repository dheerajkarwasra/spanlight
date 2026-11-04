"""Reserve a gateway call's estimated cost against its `block` budgets, then settle the real cost.

The guard locks counters in budget order, then reads one fresh statement per budget. Untracked
matching spans, retained correlated charges, and held reservations form disjoint terms. A
counter's legacy carry is conservative because old code deleted settlement identities. Missing
history refuses admission; evaluator snapshots never supply an accounting baseline.

`settle` runs in the span transaction. It transitions held/reaped correlation to settled,
retaining the final charge while raw data exists and for at least 90 days from admission.
A reaped estimate is corrected by `charge - amount`; retrying a settled row changes nothing.
Stored cost corrections are read by admission; the retained charge is the fallback when the
cost is unavailable.

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
from sqlalchemy import Row, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.compaction import HOUR
from app.budgets.history import read_history
from app.budgets.periods import next_reset, period_start
from app.budgets.types import BudgetPeriod, BudgetScope
from app.core.ids import new_id
from app.core.observability import BUDGET_RESERVE_PATH
from app.db.models import BudgetBucketDelta, BudgetCounter, BudgetReservation, Span
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
HELD, REAPED, SETTLED = "held", "reaped", "settled"

_INPUT_KEYS = ("messages", "input", "system", "instructions", "tools")
_OUTPUT_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
# Content parts that carry no text: images, files and audio are not counted (settle charges
# the real cost).
_SKIPPED_PARTS = frozenset(
    {"image", "image_url", "input_image", "input_file", "file", "document", "input_audio", "audio"}
)


@dataclass(frozen=True, slots=True)
class BlockBudget:
    """A matching block budget as the guard read and locked it."""

    id: uuid.UUID
    name: str
    period: BudgetPeriod
    amount_usd: Decimal
    scope: BudgetScope = BudgetScope.PROJECT
    scope_id: str | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    """The budget the call did not fit, what is committed against it and the call's estimate."""

    budget: BlockBudget
    committed_usd: Decimal
    estimate_usd: Decimal
    history_unknown: bool = False
    legacy_uncertain: bool = False


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


_FAST = text("""
    SELECT * FROM public.budget_reserve_fast(
        CAST(:project_id AS uuid), CAST(:budget_ids AS uuid[]), CAST(:periods AS timestamptz[]),
        CAST(:lasts AS timestamptz[]), CAST(:amounts AS numeric[]), CAST(:monthly AS boolean[]),
        CAST(:estimate AS numeric), CAST(:trace_id AS text), CAST(:span_id AS text),
        CAST(:ids AS uuid[]), CAST(:expires_at AS timestamptz), CAST(:now AS timestamptz))
""")


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
    """Reserve in one round trip, repairing stale or missing history on the slow path.

    The caller holds the budgets FOR SHARE under the project binding. The VOLATILE function
    locks every counter before reading history in fresh READ COMMITTED statement snapshots.
    """
    periods = [period_start(budget.period, now) for budget in budgets]
    last = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + HOUR
    row = (
        await db.execute(
            _FAST,
            {
                "project_id": project_id,
                "budget_ids": [budget.id for budget in budgets],
                "periods": periods,
                "lasts": [
                    min(max(last, started), next_reset(budget.period, now) - HOUR)
                    for budget, started in zip(budgets, periods, strict=True)
                ],
                "amounts": [budget.amount_usd for budget in budgets],
                "monthly": [budget.period is BudgetPeriod.MONTHLY for budget in budgets],
                "estimate": estimate_usd,
                "trace_id": trace_id,
                "span_id": span_id,
                "ids": [new_id() for _ in budgets],
                "expires_at": now + RESERVATION_LEASE,
                "now": now,
            },
        )
    ).one()
    if row.status == "slow":
        BUDGET_RESERVE_PATH.labels(path="slow").inc()
        return await _reserve_slow(
            db, project_id, budgets, estimate_usd, trace_id=trace_id, span_id=span_id, now=now
        )
    if row.status == "reserved":
        BUDGET_RESERVE_PATH.labels(path="fast").inc()
        return None
    if row.status == "refused":
        BUDGET_RESERVE_PATH.labels(path="fast").inc()
        return Refusal(
            next(budget for budget in budgets if budget.id == row.budget_id),
            row.committed_usd,
            estimate_usd,
            history_unknown=row.history_unknown,
            legacy_uncertain=row.legacy_uncertain,
        )
    raise ValueError(f"Unexpected budget reserve status: {row.status}")


async def _reserve_slow(
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
    `FOR SHARE` on the budgets (the guard), so none is edited or deleted meanwhile.

    Three statements per budget once its period's counter row exists: lock the counter, read
    the period's hours (`read_history`, plus its compaction when hours are touched), and write
    the hold with the reservation rows. The first lock takes every counter row or none; the
    first admission of a period then adds the insert and a second lock. The lock is a statement
    of its own on purpose: the hours read then runs in a snapshot taken after the lock was
    granted, so a settle or reap that committed while this admission waited is seen whole (its
    counter move and its delta or bump together)."""
    periods = {budget.id: period_start(budget.period, now) for budget in budgets}
    keys = sorted(periods.items())
    counters = await _lock_counters(db, keys, all_or_none=True)
    if keys and not counters:
        # The first admission of a period: create the missing counter rows, then lock them all.
        # Nothing is locked yet, and both the inserts and the lock go in budget order, so
        # admissions over overlapping budgets wait for each other in one order (no deadlock).
        # A concurrent first admission waits here for this insert to commit, then reads the row.
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
        counters = await _lock_counters(db, keys)
    budgets = [budget for budget in budgets if budget.id in counters]  # none gone, in practice
    for budget in budgets:  # the guard's order (amount, then id): the refusal names the first
        history = await read_history(
            db,
            project_id,
            budget.id,
            budget.scope,
            budget.scope_id,
            periods[budget.id],
            next_reset(budget.period, now),
            now=now,
        )
        minimum_days = 31 if budget.period is BudgetPeriod.MONTHLY else 1
        unknown = (
            history.missing_through is not None and history.missing_through >= periods[budget.id]
        ) or history.retention_days < minimum_days
        if unknown or history.committed_usd + estimate_usd > budget.amount_usd:
            return Refusal(
                budget,
                history.committed_usd,
                estimate_usd,
                history_unknown=unknown,
                legacy_uncertain=history.legacy_usd > 0,
            )
    # One statement: the counters' hold and the reservation rows (independent writes).
    await db.execute(
        _HOLD,
        {
            "key_ids": [budget_id for budget_id, _ in keys],
            "key_periods": [started for _, started in keys],
            "ids": [new_id() for _ in budgets],
            "budget_ids": [budget.id for budget in budgets],
            "periods": [periods[budget.id] for budget in budgets],
            "project_id": project_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "amount": estimate_usd,
            "now": now,
            "expires_at": now + RESERVATION_LEASE,
        },
    )
    return None


# Fixed text with array parameters, so it is compiled once per process instead of once per call.
_HOLD = text(f"""
    WITH reserved AS (
        UPDATE budget_counters SET reserved_usd = reserved_usd + :amount, updated_at = :now
        WHERE (budget_id, period_start) IN (
            SELECT * FROM unnest(CAST(:key_ids AS uuid[]), CAST(:key_periods AS timestamptz[]))))
    INSERT INTO budget_reservations
           (id, project_id, budget_id, period_start, trace_id, span_id, amount_usd, status,
            expires_at)
    SELECT r.id, :project_id, r.budget_id, r.period_start, :trace_id, :span_id, :amount,
           '{HELD}', :expires_at
    FROM unnest(CAST(:ids AS uuid[]), CAST(:budget_ids AS uuid[]),
                CAST(:periods AS timestamptz[])) AS r(id, budget_id, period_start)
""")


async def _lock_counters(
    db: AsyncSession, keys: Sequence[tuple[uuid.UUID, datetime]], *, all_or_none: bool = False
) -> dict[uuid.UUID, Row[uuid.UUID, Decimal, Decimal]]:
    """The counter rows of `keys` that exist, locked `FOR UPDATE` in budget order (spec D4).
    With `all_or_none`, nothing is locked unless every row exists (one statement still)."""
    matching = tuple_(BudgetCounter.budget_id, BudgetCounter.period_start).in_(keys)
    query = select(
        BudgetCounter.budget_id, BudgetCounter.settled_usd, BudgetCounter.reserved_usd
    ).where(matching)
    if all_or_none:
        existing = select(func.count()).select_from(BudgetCounter).where(matching)
        query = query.where(existing.scalar_subquery() == len(keys))
    rows = await db.execute(query.order_by(BudgetCounter.budget_id).with_for_update())
    return {row.budget_id: row for row in rows}


async def settle(
    db: AsyncSession, project_id: uuid.UUID, span: Mapping[str, Any], now: datetime
) -> None:
    """Replace the call's reservations with what its stored span cost. Runs after the span is
    written, in its transaction; a call that reserved nothing (or settled already) writes nothing.
    """
    rows = (
        (
            await db.execute(
                select(BudgetReservation)
                .where(
                    BudgetReservation.project_id == project_id,
                    BudgetReservation.trace_id == span.get("trace_id"),
                    BudgetReservation.span_id == span.get("span_id"),
                )
                .where(BudgetReservation.status != SETTLED)
                .order_by(BudgetReservation.budget_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    stored = (
        await db.execute(
            select(Span.cost_usd, Span.usage_rollup).where(
                Span.project_id == project_id,
                Span.trace_id == span.get("trace_id"),
                Span.span_id == span.get("span_id"),
            )
        )
    ).first()
    cost = None if stored is None else stored.cost_usd
    for row in sorted(rows, key=lambda row: row.budget_id):
        amount = Decimal(row.amount_usd)
        charge = (
            Decimal(cost)
            if cost is not None
            else failed_call_charge(
                may_have_billed=_may_have_billed(span), usage_cost=None, estimate=amount
            )
        )
        row.charged_usd = charge
        # A reaped row was counted at its estimate already: only the difference is added.
        held = row.status == HELD
        released, counted = (amount, charge) if held else (Decimal(0), charge - amount)
        row.status = SETTLED
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
        # A held row counted nothing in its admission hour until now: the charge is the hour's
        # exact change, appended as a delta (the reservation trigger bumps nothing for this
        # transition). A reaped or reclassified row's hour is bumped by the trigger instead.
        if held and not row.reclassified:
            await db.execute(
                insert(BudgetBucketDelta).values(
                    project_id=project_id,
                    budget_id=row.budget_id,
                    bucket_start=_admission_hour(row.expires_at),
                    amount_usd=Decimal(0) if stored is not None and stored.usage_rollup else charge,
                )
            )


def _admission_hour(expires_at: datetime) -> datetime:
    """The UTC hour a reservation was admitted in: the lease back from its expiry."""
    admitted = (expires_at - RESERVATION_LEASE).astimezone(UTC)
    return admitted.replace(minute=0, second=0, microsecond=0)


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
            # The project's `KEY SHARE` first, as the recorder's transaction holds it before
            # its settle: a budget edit's fence then waits for this settle instead of
            # deadlocking with it through the reservation rows it holds.
            await db.execute(
                text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE"),
                {"project_id": project_id},
            )
            await settle(db, project_id, span, datetime.now(UTC))
    except Exception as failure:  # noqa: BLE001 - the reaper covers it
        logger.warning(
            "budget.settle_unstored_failed",
            span_id=span.get("span_id"),
            error_type=type(failure).__name__,
        )
