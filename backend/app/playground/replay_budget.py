"""A project's replay budget as the dashboard reads it (spec §6.6)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.budget import month_start, remaining
from app.playground.replay_queries import month_spend


@dataclass(frozen=True, slots=True)
class ReplayBudget:
    """The month so far: the budget, what settled replays cost, what running ones hold, and what
    is left (never below zero)."""

    monthly_budget_usd: Decimal
    spent_usd: Decimal
    reserved_usd: Decimal
    remaining_usd: Decimal
    month_start: datetime


async def read_budget(
    db: AsyncSession, project_id: uuid.UUID, *, budget: Decimal, now: datetime
) -> ReplayBudget:
    """The project's replay budget for `now`'s month. The caller binds the project."""
    spend = await month_spend(db, project_id, now)
    return ReplayBudget(
        monthly_budget_usd=budget,
        spent_usd=spend.spent,
        reserved_usd=spend.reserved,
        remaining_usd=remaining(budget, spend.committed),
        month_start=month_start(now),
    )
