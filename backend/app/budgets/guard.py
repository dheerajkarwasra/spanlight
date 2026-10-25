"""The gateway's budget guard: refuse a call its `block` budgets cannot afford.

`StateBudgetGuard` makes two checks in the gateway's short transaction, which already runs
under the project binding so row-level security on `budgets`, `alert_states` and the counter
tables applies. First the evaluated state: a budget whose hidden alert rule is firing, and began
firing in the current UTC period, refuses the call at once (ADR 0012; the message says it is the
measured, soft limit, since SDK spend is counted but never stopped). Then the reservation
(`app.budgets.reserve`, ADR 0017): every matching enabled block budget is read with the spend
its rule last measured this period, the call's cost is estimated from its body, and the estimate
is reserved against each budget's counter row or the call is refused, so any number of
concurrent calls across replicas cannot pass the cap together. A call that matches no block
budget, or whose model has no price, reserves nothing and is admitted as before.

A `model` budget applies to a call when its name matches the requested model or any target's
model, with the snapshot rule of `app.core.model_match`. A `metadata` budget applies when its
`key=value` pair is one of the call's, and a `trace_name` budget when its name is the call's
(spec §8.3); the gateway supplies both from the headers and the stored trace. Ingestion never
calls the guard.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, and_, any_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.formatting import format_money
from app.alerts.types import RuleStateName
from app.budgets.periods import period_start
from app.budgets.reserve import BlockBudget, Refusal, estimate, reserve
from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope
from app.config import Settings
from app.core.model_match import SNAPSHOT_SUFFIX_REGEX
from app.core.observability import BUDGET_BLOCKS, BUDGET_RESERVE_DURATION
from app.db.models.alerts import AlertState
from app.db.models.budgets import Budget
from app.gateway.budget import BudgetDecision, BudgetGuard
from app.pricing.cost import load_price_book

# How often the budgets' alert rules measure spend; the block message quotes it. Re-measured
# numbers belong here and in `frontend/src/features/budgets/budget-enforcement.ts`.
EVALUATION_INTERVAL_S = 60
_SOFT_LIMIT = f"(soft limit, measured every {EVALUATION_INTERVAL_S} s)"
_PERIOD_WORDS = {BudgetPeriod.DAILY: "today", BudgetPeriod.MONTHLY: "this month"}


def _model_budget_matches(models: Sequence[str]) -> ColumnElement[bool]:
    """A `model` budget's name equals one of `models` or is that name minus a snapshot suffix.

    The rule of `app.core.model_match`, with the configured name in the column: `starts_with`
    and the suffix check are plain functions of the bound name, so nothing needs escaping.
    """
    return or_(
        *(
            or_(
                Budget.scope_id == name,
                and_(
                    func.starts_with(name, Budget.scope_id),
                    func.substr(name, func.length(Budget.scope_id) + 1).op("~")(
                        SNAPSHOT_SUFFIX_REGEX
                    ),
                ),
            )
            for name in models
        )
    )


def _scope_match(
    key_id: UUID | None,
    models: Sequence[str],
    external_user_id: str | None,
    metadata: Mapping[str, str] | None,
    trace_name: str | None,
) -> ColumnElement[bool]:
    """The condition selecting the budgets that apply to one call.

    A scope whose value is unknown (no key, no end user, no metadata, no name) is left out:
    `scope_id = NULL` would match nothing anyway, and leaving it out keeps the query plain. A
    metadata pair is compared as the `key=value` string the scope stores, in one array bind.
    """
    clauses = [
        Budget.scope == BudgetScope.PROJECT,
        and_(Budget.scope == BudgetScope.MODEL, _model_budget_matches(models)),
    ]
    if key_id is not None:
        clauses.append(
            and_(Budget.scope == BudgetScope.GATEWAY_KEY, Budget.scope_id == str(key_id))
        )
    if external_user_id is not None:
        clauses.append(and_(Budget.scope == BudgetScope.USER, Budget.scope_id == external_user_id))
    if metadata:
        pairs = literal([f"{key}={value}" for key, value in metadata.items()], ARRAY(Text))
        clauses.append(and_(Budget.scope == BudgetScope.METADATA, Budget.scope_id == any_(pairs)))
    if trace_name is not None:
        clauses.append(and_(Budget.scope == BudgetScope.TRACE_NAME, Budget.scope_id == trace_name))
    return or_(*clauses)


def blocked_reason(name: str, period: BudgetPeriod, spent: Decimal | None, amount: Decimal) -> str:
    """The client-facing sentence for a budget whose measured spend is over the cap, without a
    trailing period.

    It ends with the soft-limit suffix, added here once so the gateway's 402 and the demo job's
    skip say the same thing: this refusal comes from the evaluated state, which counts spend the
    gateway could not stop (the SDK's).
    """
    when = _PERIOD_WORDS[period]
    head = f'Budget "{name}" is exhausted'
    cap = format_money(amount, plain=True)
    if spent is None:
        return f"{head}: the {cap} limit is reached {when} {_SOFT_LIMIT}"
    return f"{head}: {format_money(spent, plain=True)} of {cap} spent {when} {_SOFT_LIMIT}"


def refused_reason(refusal: Refusal) -> str:
    """The client-facing sentence for a call whose estimate did not fit, without a trailing
    period: what is committed, what this call would add and the cap, and how to reserve less."""
    budget = refusal.budget
    return (
        f'Budget "{budget.name}" would be exceeded: '
        f"{format_money(refusal.committed_usd, plain=True)} spent or in flight plus "
        f"{format_money(refusal.estimate_usd, plain=True)} for this call is over the "
        f"{format_money(budget.amount_usd, plain=True)} limit {_PERIOD_WORDS[budget.period]}; "
        "a lower max_tokens lowers the reservation"
    )


class StateBudgetGuard:
    """Refuses a call when a matching enabled `block` budget is firing or cannot hold its
    estimate."""

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
    ) -> BudgetDecision:
        # The model asked for, then each target's name after its alias, without repeats.
        models = list(dict.fromkeys([model, *target_models]))
        statement = (
            select(
                Budget.id,
                Budget.name,
                Budget.period,
                Budget.amount_usd,
                AlertState.state,
                AlertState.since,
                AlertState.last_value,
                AlertState.last_evaluated_at,
            )
            .outerjoin(AlertState, AlertState.rule_id == Budget.rule_id)
            .where(
                Budget.project_id == project_id,
                Budget.enabled.is_(True),
                Budget.action == BudgetAction.BLOCK,
                _scope_match(key_id, models, external_user_id, metadata, trace_name),
            )
            .order_by(Budget.amount_usd, Budget.id)
            # Waits for an edit or deletion of a matching budget to commit (and re-reads it),
            # and keeps one from starting until this check commits: an edit locks the budget
            # then its counters, so locking the counters first would deadlock on the
            # reservation's foreign key.
            .with_for_update(key_share=True, of=Budget)
        )
        rows = (await db.execute(statement)).all()
        if not rows:
            return BudgetDecision(allowed=True)
        # A state from a period that has ended only waits for the next evaluation to resolve it.
        starts = {row.id: period_start(row.period, now) for row in rows}
        firing = next(
            (
                row
                for row in rows
                if row.state == RuleStateName.FIRING
                and row.since is not None
                and row.since >= starts[row.id]
            ),
            None,
        )
        if firing is not None:
            BUDGET_BLOCKS.labels("firing").inc()
            return BudgetDecision(
                allowed=False,
                budget_id=firing.id,
                reason=blocked_reason(
                    firing.name, firing.period, firing.last_value, firing.amount_usd
                ),
            )
        if body is None or trace_id is None or span_id is None:
            return BudgetDecision(allowed=True)  # nothing to estimate or settle against
        with BUDGET_RESERVE_DURATION.time():
            prices = await load_price_book(db, org_id)
            targets = zip(target_providers, target_models, strict=False)  # () from old callers
            candidates = list(dict.fromkeys([(None, model), *targets]))
            estimate_usd = estimate(body, candidates, prices, at=now)
            if estimate_usd is None or estimate_usd <= 0:
                # Unpriced: admitted as unknown (ADR 0012). Free ($0 override): nothing to hold.
                return BudgetDecision(allowed=True)
            budgets = [
                BlockBudget(
                    id=row.id,
                    name=row.name,
                    period=row.period,
                    amount_usd=row.amount_usd,
                    measured_usd=_measured(row, starts[row.id]),
                )
                for row in rows
            ]
            refusal = await reserve(
                db, project_id, budgets, estimate_usd, trace_id=trace_id, span_id=span_id, now=now
            )
        if refusal is None:
            return BudgetDecision(allowed=True)
        BUDGET_BLOCKS.labels("reserved").inc()
        return BudgetDecision(
            allowed=False, budget_id=refusal.budget.id, reason=refused_reason(refusal)
        )


def _measured(row: Any, started: datetime) -> Decimal:
    """M: the spend the rule last measured, if that measurement was taken this period."""
    if row.last_value is None or row.last_evaluated_at is None or row.last_evaluated_at < started:
        return Decimal(0)
    return Decimal(row.last_value)


def get_budget_guard(settings: Settings) -> BudgetGuard:
    """The guard the gateway runtime uses. Settings are accepted for a future switch; none yet."""
    return StateBudgetGuard()
