"""Release comparison: the releases seen in a window and how two of them differ.

Decimals that are not money (deltas of rates and latencies) are strings in JSON like money is, so
a client never sees a float rounded differently from the server's arithmetic.
"""

from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.common import Money


class ReleaseStats(BaseModel):
    """One release over a window. Unknown values are `None`, never 0.

    `cost_usd` is the sum of the priced spans: `None` only when nothing was priced, and a lower
    bound while `unpriced_calls` is above 0.
    """

    release: str
    first_seen_at: datetime
    last_seen_at: datetime
    traces: int
    llm_calls: int
    unpriced_calls: int
    error_rate: float | None
    p50_ms: float | None
    p95_ms: float | None
    cost_usd: Money | None
    input_tokens: int
    output_tokens: int


class Delta(BaseModel):
    """`b - a`, and that as a fraction of `a` (`None` when `a` is 0)."""

    absolute: Money
    relative: Money | None


class ModelShare(BaseModel):
    """A model's share of each release's LLM calls (`None`: that release made no calls)."""

    model: str | None
    a_share: float | None
    b_share: float | None


class ErrorClassChange(BaseModel):
    error_class: str | None
    a_count: int
    b_count: int


class NewError(BaseModel):
    """A normalised failure message that spans of `b` have and spans of `a` do not."""

    message: str
    count: int
    example_trace_id: str


class Comparison(BaseModel):
    a: ReleaseStats
    b: ReleaseStats
    deltas: dict[str, Delta | None]
    model_mix: list[ModelShare]
    error_classes: list[ErrorClassChange]
    new_errors: list[NewError]


class CategoryRateOut(BaseModel):
    """`count` of `reviewed` traces as a rate at 4 places, with its Wilson 95 % bounds."""

    count: int
    rate: Money
    ci_low: Money
    ci_high: Money


class ReleaseRatesOut(BaseModel):
    """One release's reviewed traces (`release: None` is the traces without a release) and the
    rate of each failure category among them; `rates` is empty when nothing was reviewed."""

    release: str | None
    reviewed: int
    rates: dict[str, CategoryRateOut]


class FailureCategoriesOut(BaseModel):
    """`categories` is the config's list in its order; a label outside it still appears in
    `rates` after the configured ones."""

    score_name: str
    categories: list[str]
    releases: list[ReleaseRatesOut]
