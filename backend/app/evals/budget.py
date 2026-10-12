"""The pure rules of a judge's monthly budget (ADR 0015).

A judge run reserves an estimate of its cost before calling the provider and is admitted only
when the month's committed spend plus that estimate stays within the budget. Input tokens are
estimated by `app.pricing.tokens` (3 ASCII characters per token, every other character one
token; the Doctor's explanations use the same rule). It is meant as a ceiling for prose but is
not a strict one for code- or digit-heavy text, so a budget can be overshot by that slack; the
whole `max_tokens` allowance counts as output. The database side of the reservation lives in
`app.evals.reservation`.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.pricing.cost import PriceBook
from app.pricing.tokens import estimate_input_tokens, estimate_text_tokens


def month_start(now: datetime) -> datetime:
    """The first instant of `now`'s calendar month in UTC; budgets reset there."""
    utc = now.astimezone(UTC)
    return utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def estimate_call_cost(
    model: str,
    input: str | int,
    max_output_tokens: int,
    prices: PriceBook,
    *,
    provider: str | None,
    at: datetime,
) -> Decimal | None:
    """The most one call can cost: the estimated input tokens and every allowed output token at
    the model's price. None when the model has no price (the budget could not be enforced).

    `input` is the compiled prompt's text; a number counts that many ASCII characters (for a
    check that has no text yet, such as validating a judge's model).
    """
    tokens = estimate_text_tokens(input) if isinstance(input, str) else estimate_input_tokens(input)
    cost = prices.cost(
        provider=provider,
        model=model,
        at=at,
        input_tokens=tokens,
        output_tokens=max_output_tokens,
        cached_tokens=None,
    )
    return None if cost is None else cost.cost_usd


def can_spend(committed: Decimal, estimate: Decimal, budget: Decimal) -> bool:
    """Whether a run estimated at `estimate` fits next to the month's `committed` spend."""
    return committed + estimate <= budget


def remaining(budget: Decimal, committed: Decimal) -> Decimal:
    """What is left of the budget, never below zero."""
    return max(budget - committed, Decimal(0))


def failed_call_charge(
    *, may_have_billed: bool, usage_cost: Decimal | None, estimate: Decimal
) -> Decimal:
    """What a judge call that produced no verdict is charged when it settles.

    A call the gateway never sent, or one the provider refused with an HTTP error status, cost
    nothing and releases its reservation. Any other failure (a timeout, a connection error, a
    cancellation, an unparseable verdict) may have been billed: the reported usage's cost when
    there is one, else the reserved estimate, the worst case.
    """
    if not may_have_billed:
        return Decimal(0)
    if usage_cost is not None:
        return max(usage_cost, Decimal(0))
    return estimate
