"""Budgets: a project's spend caps and where each stands in its period.

The create body, `BudgetSpec`, and the edit body, `BudgetPatch`, are owned by the budgets domain
(`app.budgets.schemas`), which validates them; they are re-exported here so routers keep
importing from `app.api.schemas`. Money is a decimal string without an exponent; spend that could
not be measured is `null`, never `"0"`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from app.alerts.payload import DecimalStr
from app.alerts.types import RuleStateName
from app.api.schemas.common import ApiModel
from app.budgets.periods import next_reset, period_start
from app.budgets.queries import Counters
from app.budgets.schemas import BudgetPatch, BudgetSpec
from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope
from app.db.models import AlertState, Budget
from app.db.models.budgets import BudgetCounter

__all__ = [
    "BudgetBurnOut",
    "BudgetOut",
    "BudgetPatch",
    "BudgetSpec",
    "BudgetStateOut",
    "BurnPointOut",
]


class BudgetStateOut(ApiModel):
    """Where the budget stands in the current period (by the server's clock).

    Until the first evaluation of a new period, the previous period's figures no longer apply:
    the state shows the current period with `spent_usd` null and `state` `ok` (a firing alert
    from a past period ends on that evaluation and never blocks).

    `reserved_usd` and `gateway_settled_usd` are the budget's gateway counter this period
    (ADR 0017): only a `block` budget has one, so both are null for a `notify` budget. A `block`
    budget that admitted no gateway call this period reads `"0"` for both.
    """

    # Spend so far in the period; null when not measured yet in this period, or when it could
    # not be measured (spans, none priced).
    spent_usd: DecimalStr | None
    period_start: datetime
    resets_at: datetime
    # `firing` once the spend passed the amount (a `block` budget then blocks gateway calls).
    state: RuleStateName
    last_evaluated_at: datetime
    # Gateway estimates held in flight against this budget this period; null for `notify`.
    reserved_usd: DecimalStr | None = None
    # Gateway charges settled into the counter this period; null for `notify`.
    gateway_settled_usd: DecimalStr | None = None

    @classmethod
    def of(
        cls,
        budget: Budget,
        state: AlertState | None,
        now: datetime,
        counter: BudgetCounter | None = None,
    ) -> Self | None:
        """The state of `budget` at `now` from its rule's state row and its current period's
        gateway counter; None before its first evaluation."""
        if state is None or state.last_evaluated_at is None:
            return None
        evaluated = state.last_evaluated_at
        start = period_start(budget.period, now)
        current = period_start(budget.period, evaluated) >= start
        counts = budget.action is BudgetAction.BLOCK
        return cls(
            spent_usd=state.last_value if current else None,
            period_start=start,
            resets_at=next_reset(budget.period, now),
            state=state.state if current else RuleStateName.OK,
            last_evaluated_at=evaluated,
            reserved_usd=(counter.reserved_usd if counter else Decimal(0)) if counts else None,
            gateway_settled_usd=(counter.settled_usd if counter else Decimal(0))
            if counts
            else None,
        )


class BudgetOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    scope: BudgetScope
    # Null exactly when `scope` is `project`: otherwise the gateway key id, end user, model,
    # `key=value` metadata pair or trace name.
    scope_id: str | None
    period: BudgetPeriod
    amount_usd: DecimalStr
    action: BudgetAction
    enabled: bool
    channel_ids: list[uuid.UUID]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # Null until the budget's first evaluation.
    state: BudgetStateOut | None = None

    @classmethod
    def of(
        cls,
        budget: Budget,
        state: AlertState | None,
        now: datetime,
        counters: Counters | None = None,
    ) -> Self:
        counter = (counters or {}).get((budget.id, period_start(budget.period, now)))
        return cls.model_validate(budget).model_copy(
            update={"state": BudgetStateOut.of(budget, state, now, counter)}
        )


class BurnPointOut(ApiModel):
    """One UTC day of the period; the first and the current day can be partial."""

    day: date
    # The day's spend; null when it had spans and none could be priced.
    spend_usd: DecimalStr | None
    # The known days' spend so far, this one included; null until the first known day.
    cumulative_usd: DecimalStr | None


class BudgetBurnOut(ApiModel):
    """The budget's spend per day of the current period, up to now, and a month-end forecast."""

    period_start: datetime
    resets_at: datetime
    amount_usd: DecimalStr
    points: list[BurnPointOut]
    # The spend at the period's end at the trailing complete days' rate; null for a daily
    # budget, under 3 days of spend history, or when one of those days is unknown.
    forecast_usd: DecimalStr | None
    # How many complete days the rate is the mean of (up to 7).
    forecast_basis_days: int
    # The first UTC day the spend passes the amount, measured or forecast; null if it does not
    # within the period.
    forecast_crosses_on: date | None
