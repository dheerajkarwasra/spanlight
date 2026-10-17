"""The playground's response. The request body is owned by `app.playground.schemas`."""

from datetime import datetime
from decimal import Decimal

from app.api.schemas.common import ApiModel, Money
from app.gateway.sse import Usage
from app.playground.replay_budget import ReplayBudget
from app.playground.schemas import PlaygroundResult, PlaygroundRunIn

__all__ = ["PlaygroundResultOut", "PlaygroundRunIn", "PlaygroundRunOut", "ReplayBudgetOut"]


class UsageOut(ApiModel):
    input_tokens: int
    # None when the provider never reported its final output count; the cost is then None too.
    output_tokens: int | None
    # None when the provider sent no cache detail.
    cached_tokens: int | None
    # Prompt tokens written to the provider's cache, and the part written with the 1-hour
    # lifetime. Both None when the provider did not report them, never 0.
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None

    @classmethod
    def of(cls, usage: Usage) -> "UsageOut":
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_write_1h_tokens=usage.cache_write_1h_tokens,
        )


class PlaygroundResultOut(ApiModel):
    """One configuration's run, in the order of the request. `error` set means no `output`."""

    output: str | None
    usage: UsageOut | None
    # None when the model has no price or the provider reported no usage; never 0.
    cost_usd: Money | None
    latency_ms: float | None
    # Always None: responses are not streamed yet.
    ttft_ms: float | None
    trace_id: str | None
    span_id: str | None
    # `BUDGET_EXCEEDED`, `missing_variables: a, b`, or the provider's failure.
    error: str | None

    @classmethod
    def of(cls, result: PlaygroundResult) -> "PlaygroundResultOut":
        cost: Decimal | None = result.cost_usd
        return cls(
            output=result.output,
            usage=UsageOut.of(result.usage) if result.usage is not None else None,
            cost_usd=cost,
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            trace_id=result.trace_id,
            span_id=result.span_id,
            error=result.error,
        )


class PlaygroundRunOut(ApiModel):
    results: list[PlaygroundResultOut]


class ReplayBudgetOut(ApiModel):
    """The project's replay budget for the current UTC month (spec §6.6)."""

    monthly_budget_usd: Money
    # What settled replays were charged this month.
    spent_usd: Money
    # What running replays hold until they settle.
    reserved_usd: Money
    # The budget minus both, never below zero.
    remaining_usd: Money
    month_start: datetime

    @classmethod
    def of(cls, budget: ReplayBudget) -> "ReplayBudgetOut":
        return cls(
            monthly_budget_usd=budget.monthly_budget_usd,
            spent_usd=budget.spent_usd,
            reserved_usd=budget.reserved_usd,
            remaining_usd=budget.remaining_usd,
            month_start=budget.month_start,
        )
