"""Problem responses for the errors the replay service raises (spec §9), used by
`api/v1/replays.py`."""

from app.core.errors import ProblemError
from app.playground.replay_errors import ReplayRefusedError
from app.playground.replay_service import ReplayBudgetExceededError


def replay_problem(error: ReplayRefusedError | ReplayBudgetExceededError) -> ProblemError:
    """`422 REPLAY_*` for a refusal; `409 REPLAY_BUDGET_EXCEEDED`, with the month's figures as
    extension members, when the project's replay budget cannot take the call."""
    if isinstance(error, ReplayRefusedError):
        return ProblemError(422, error.code, error.detail)
    spend = error.spend
    return ProblemError(
        409,
        "REPLAY_BUDGET_EXCEEDED",
        f"The project's replay budget of ${error.budget} for this month is used up.",
        extensions={
            "spent_usd": format(spend.spent, "f"),
            "reserved_usd": format(spend.reserved, "f"),
            "budget_usd": format(error.budget, "f"),
            "month_start": error.month_start.isoformat(),
        },
    )
