"""Budgets: a project's spend caps and where each stands in its period.

The create body, `BudgetSpec`, and the edit body, `BudgetPatch`, are owned by the budgets domain
(`app.budgets.schemas`), which validates them; they are re-exported here so routers keep
importing from `app.api.schemas`. Money is a decimal string without an exponent; spend that could
not be measured is `null`, never `"0"`.
"""

import uuid
from datetime import datetime
from typing import Self

from app.alerts.payload import DecimalStr
from app.alerts.types import RuleStateName
from app.api.schemas.common import ApiModel
from app.budgets.periods import next_reset, period_start
from app.budgets.schemas import BudgetPatch, BudgetSpec
from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope
from app.db.models import AlertState, Budget

__all__ = ["BudgetOut", "BudgetPatch", "BudgetSpec", "BudgetStateOut"]


class BudgetStateOut(ApiModel):
    """Where the budget stands in the current period (by the server's clock).

    Until the first evaluation of a new period, the previous period's figures no longer apply:
    the state shows the current period with `spent_usd` null and `state` `ok` (a firing alert
    from a past period ends on that evaluation and never blocks).
    """

    # Spend so far in the period; null when not measured yet in this period, or when it could
    # not be measured (spans, none priced).
    spent_usd: DecimalStr | None
    period_start: datetime
    resets_at: datetime
    # `firing` once the spend passed the amount (a `block` budget then blocks gateway calls).
    state: RuleStateName
    last_evaluated_at: datetime

    @classmethod
    def of(cls, budget: Budget, state: AlertState | None, now: datetime) -> Self | None:
        """The state of `budget` at `now` from its rule's state row; None before its first
        evaluation."""
        if state is None or state.last_evaluated_at is None:
            return None
        evaluated = state.last_evaluated_at
        start = period_start(budget.period, now)
        current = period_start(budget.period, evaluated) >= start
        return cls(
            spent_usd=state.last_value if current else None,
            period_start=start,
            resets_at=next_reset(budget.period, now),
            state=state.state if current else RuleStateName.OK,
            last_evaluated_at=evaluated,
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
    def of(cls, budget: Budget, state: AlertState | None, now: datetime) -> Self:
        return cls.model_validate(budget).model_copy(
            update={"state": BudgetStateOut.of(budget, state, now)}
        )
