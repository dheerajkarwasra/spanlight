"""One price row and the token arithmetic over it. Pure: no I/O.

Token convention (shared with the SDKs, see docs/api-deviations.md):
`input_tokens` is the *total* prompt size, cache reads and cache writes included;
`cached_tokens` is the part served from the provider's prompt cache (reads);
`cache_write_tokens` is the part written to it, and `cache_write_1h_tokens` the
part of those writes kept for one hour. The classes do not overlap, so

    reads    = min(cached, input)
    writes   = min(cache_write, input - reads)
    writes1h = min(cache_write_1h, writes)
    writes5m = writes - writes1h
    uncached = input - reads - writes
    cost = (uncached * input_rate + reads * cached_rate
            + writes5m * write_5m_rate + writes1h * write_1h_rate + output * output_rate) / 1e6

A missing read or write rate falls back to `input_rate`, so a model without a
published premium is priced as if the cache did not exist. A missing write
count means no writes; a missing 1-hour count means every write is 5-minute.

Some models bill the whole prompt at a higher rate once it passes a size: such
a model has one row per tier, `min_prompt_tokens` being the smallest prompt the
row prices. The row with the highest `min_prompt_tokens` not above the prompt
wins, so a long prompt picks the upper tier and a short one the base row.

An organization's price overrides are loaded next to the seed prices. Within a tier, for the same
model, the longest matching pattern wins whatever its source; between equal patterns an override
beats the seed row. An override is per tier: overriding only the base row keeps the seed's upper
tiers. An override's `version` is `override:<id>`, so a stored cost says which rate made it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.core.model_match import matches_model
from app.pricing.provider_alias import canonical_provider

_PER_MILLION = Decimal(1_000_000)

# Provider snapshots append a release date (`gpt-4o-2024-08-06`,
# `claude-haiku-4-5-20251001`) or an alias suffix. A pattern matches the model
# exactly or followed by one of these (`app.core.model_match`), and the longest matching
# pattern wins. Plain prefix matching would wrongly price e.g. `gpt-4.1-nano` as `gpt-4.1`.

OVERRIDE_VERSION_PREFIX = "override:"


@dataclass(frozen=True)
class Price:
    provider: str
    model_pattern: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None
    effective_from: datetime
    version: str
    source: Literal["seed", "override"] = "seed"
    cache_write_5m_per_mtok: Decimal | None = None
    cache_write_1h_per_mtok: Decimal | None = None
    # The smallest prompt this row prices; 0 for the base row of a model.
    min_prompt_tokens: int = 0

    def matches(self, model: str) -> bool:
        return matches_model(self.model_pattern, model)

    @property
    def cached_rate(self) -> Decimal:
        """The cache-read rate, or the input rate when none is published."""
        return _or_input(self.cached_input_per_mtok, self.input_per_mtok)

    @property
    def write_5m_rate(self) -> Decimal:
        """The 5-minute cache-write rate, or the input rate when none is published."""
        return _or_input(self.cache_write_5m_per_mtok, self.input_per_mtok)

    @property
    def write_1h_rate(self) -> Decimal:
        """The 1-hour cache-write rate, or the input rate when none is published."""
        return _or_input(self.cache_write_1h_per_mtok, self.input_per_mtok)


def _or_input(rate: Decimal | None, input_rate: Decimal) -> Decimal:
    return rate if rate is not None else input_rate


def best_price(
    prices: Iterable[Price], provider: str | None, model: str, at: datetime, prompt_tokens: int
) -> Price | None:
    """The row of `prices` that prices `model` at `at`, or None.

    The span's provider and each row's go through `canonical_provider`, so an override saved
    under an alias matches every spelling of it; no provider matches every row. Among the rows
    in force, the highest `min_prompt_tokens` not above the prompt wins, then the longest pattern,
    then an override over a seed row, then the latest `effective_from`, then the greatest
    `version`. The last key only separates overrides saved under two spellings of one provider
    (`gemini` and `google`); it keeps the winner independent of the order rows were loaded in.
    """
    canonical = canonical_provider(provider)
    candidates = [
        price
        for price in prices
        if price.effective_from <= at
        and price.min_prompt_tokens <= prompt_tokens
        and (canonical is None or canonical_provider(price.provider) == canonical)
        and price.matches(model)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda price: (
            price.min_prompt_tokens,
            len(price.model_pattern),
            price.source == "override",
            price.effective_from,
            price.version,
        ),
    )


@dataclass(frozen=True)
class TokenClasses:
    """A prompt split into the classes that are billed at different rates; they do not overlap."""

    uncached: int
    reads: int
    writes_5m: int
    writes_1h: int


def token_classes(
    input_tokens: int,
    cached_tokens: int | None,
    cache_write_tokens: int | None,
    cache_write_1h_tokens: int | None,
) -> TokenClasses:
    """The prompt's classes. Each is clamped to what the prompt leaves, so none goes negative."""
    reads = min(cached_tokens or 0, input_tokens)
    writes = min(cache_write_tokens or 0, input_tokens - reads)
    writes_1h = min(cache_write_1h_tokens or 0, writes)
    return TokenClasses(
        uncached=input_tokens - reads - writes,
        reads=reads,
        writes_5m=writes - writes_1h,
        writes_1h=writes_1h,
    )


def token_cost(
    price: Price,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None,
    cache_write_tokens: int | None,
    cache_write_1h_tokens: int | None,
) -> Decimal:
    """The unrounded standard-tier token cost in USD."""
    split = token_classes(input_tokens, cached_tokens, cache_write_tokens, cache_write_1h_tokens)
    return (
        split.uncached * price.input_per_mtok
        + split.reads * price.cached_rate
        + split.writes_5m * price.write_5m_rate
        + split.writes_1h * price.write_1h_rate
        + output_tokens * price.output_per_mtok
    ) / _PER_MILLION
