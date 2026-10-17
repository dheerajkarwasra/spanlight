"""The block message says what a `block` budget is: a soft limit measured every 60 seconds.

The suffix is added once, in `blocked_reason`, so the gateway's 402 and the demo job's skip
carry the same words. The rest of the sentence is unchanged and there is still no trailing
period: the gateway's envelope adds it.
"""

from decimal import Decimal

from app.budgets.guard import EVALUATION_INTERVAL_S, blocked_reason
from app.budgets.types import BudgetPeriod
from tests.budgets.support import BudgetWorld, chat, firing_budget

SUFFIX = "(soft limit, measured every 60 s)"


def test_blocked_reason_carries_the_soft_limit_suffix() -> None:
    assert EVALUATION_INTERVAL_S == 60
    known = blocked_reason("Acme daily", BudgetPeriod.DAILY, Decimal("201.10"), Decimal("200"))
    unknown = blocked_reason("Acme monthly", BudgetPeriod.MONTHLY, None, Decimal("200"))

    assert known == (
        'Budget "Acme daily" is exhausted: $201.10 of $200.00 spent today '
        "(soft limit, measured every 60 s)"
    )
    assert unknown == (
        'Budget "Acme monthly" is exhausted: the $200.00 limit is reached this month '
        "(soft limit, measured every 60 s)"
    )
    assert known.count(SUFFIX) == unknown.count(SUFFIX) == 1


async def test_gateway_402_message_carries_the_suffix(budget_world: BudgetWorld) -> None:
    await firing_budget(budget_world)

    refused = await chat(budget_world)

    assert refused.status_code == 402, refused.text
    assert refused.json()["error"]["message"] == (
        'Blocked by a Spanlight budget: Budget "Daily cap" is exhausted: '
        "$0.0020 of $0.0010 spent today (soft limit, measured every 60 s)."
    )
