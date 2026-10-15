"""Cost calculation for LLM spans.

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
    cost = uncached * input_rate + reads * cached_rate
         + writes5m * write_5m_rate + writes1h * write_1h_rate + output * output_rate

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

A cost is only produced when the model has a known price at the span's start
time and both input and output token counts are present. Otherwise it is
NULL: unknown is never reported as zero.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.model_match import matches_model
from app.db.models import ModelPrice, PriceOverride
from app.pricing.prices import ALL_SNAPSHOTS

_PER_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.00000001")  # numeric(14, 8)

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


@dataclass(frozen=True)
class CostResult:
    cost_usd: Decimal
    pricing_version: str


class PriceBook:
    """An immutable set of prices loaded once per ingestion batch."""

    def __init__(self, prices: Iterable[Price]) -> None:
        self._prices: Sequence[Price] = tuple(prices)

    def find(
        self, provider: str | None, model: str, at: datetime, prompt_tokens: int = 0
    ) -> Price | None:
        """The price for `model`, matched exactly as given (case included), or None.

        Ingestion strips surrounding whitespace before it stores and prices a model; nothing else
        is normalized, so a price and a model-scoped alert, rollup or budget agree on every span.
        The provider is a fixed vocabulary and is still compared lower-cased. `prompt_tokens`
        picks the tier: the row with the highest `min_prompt_tokens` not above it.
        """
        normalized_provider = provider.strip().lower() if provider else None
        candidates = [
            price
            for price in self._prices
            if price.effective_from <= at
            and price.min_prompt_tokens <= prompt_tokens
            and (normalized_provider is None or price.provider == normalized_provider)
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
            ),
        )

    def cost(
        self,
        *,
        provider: str | None,
        model: str | None,
        at: datetime,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_tokens: int | None,
        cache_write_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
    ) -> CostResult | None:
        if model is None or input_tokens is None or output_tokens is None:
            return None
        price = self.find(provider, model, at, prompt_tokens=input_tokens)
        if price is None:
            return None

        # Each class is clamped to what the prompt leaves, so no count goes negative.
        reads = min(cached_tokens or 0, input_tokens)
        writes = min(cache_write_tokens or 0, input_tokens - reads)
        writes_1h = min(cache_write_1h_tokens or 0, writes)
        writes_5m = writes - writes_1h
        uncached = input_tokens - reads - writes
        total = (
            uncached * price.input_per_mtok
            + reads * price.cached_rate
            + writes_5m * price.write_5m_rate
            + writes_1h * price.write_1h_rate
            + output_tokens * price.output_per_mtok
        ) / _PER_MILLION
        return CostResult(
            cost_usd=total.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP),
            pricing_version=price.version,
        )


async def load_price_book(session: AsyncSession, org_id: UUID | None = None) -> PriceBook:
    """The seed prices, plus the overrides of `org_id` when one is given.

    Two queries per batch: the seed table and the organization's own override rows.
    """
    prices = [
        Price(
            provider=row.provider,
            model_pattern=row.model_pattern,
            input_per_mtok=row.input_per_mtok,
            output_per_mtok=row.output_per_mtok,
            cached_input_per_mtok=row.cached_input_per_mtok,
            effective_from=row.effective_from,
            version=row.version,
            cache_write_5m_per_mtok=row.cache_write_5m_per_mtok,
            cache_write_1h_per_mtok=row.cache_write_1h_per_mtok,
            min_prompt_tokens=row.min_prompt_tokens,
        )
        for row in (await session.scalars(select(ModelPrice))).all()
    ]
    if org_id is not None:
        overrides = await session.scalars(
            select(PriceOverride).where(PriceOverride.org_id == org_id)
        )
        prices.extend(
            Price(
                provider=row.provider,
                model_pattern=row.model_pattern,
                input_per_mtok=row.input_per_mtok,
                output_per_mtok=row.output_per_mtok,
                cached_input_per_mtok=row.cached_input_per_mtok,
                effective_from=row.effective_from,
                version=f"{OVERRIDE_VERSION_PREFIX}{row.id}",
                source="override",
                cache_write_5m_per_mtok=row.cache_write_5m_per_mtok,
                cache_write_1h_per_mtok=row.cache_write_1h_per_mtok,
                min_prompt_tokens=row.min_prompt_tokens,
            )
            for row in overrides
        )
    return PriceBook(prices)


async def sync_seed_prices(session: AsyncSession) -> int:
    """Insert every bundled price snapshot, the current and the historical ones. Existing rows
    are left untouched.

    Each snapshot has its own `effective_from`, so its rows land beside the others and price the
    spans that start after it; a span stored under an older snapshot keeps its cost and version,
    and a fresh install prices an older span as an upgraded one does.
    Returns the number of rows inserted. The caller commits.
    """
    statement = (
        insert(ModelPrice)
        .values(
            [
                {
                    "id": new_id(),
                    "provider": seed.provider,
                    "model_pattern": seed.model_pattern,
                    "input_per_mtok": seed.input_per_mtok,
                    "output_per_mtok": seed.output_per_mtok,
                    "cached_input_per_mtok": seed.cached_input_per_mtok,
                    "cache_write_5m_per_mtok": seed.cache_write_5m_per_mtok,
                    "cache_write_1h_per_mtok": seed.cache_write_1h_per_mtok,
                    "min_prompt_tokens": seed.min_prompt_tokens,
                    "effective_from": snapshot.effective_from,
                    "version": snapshot.version,
                }
                for snapshot in ALL_SNAPSHOTS
                for seed in snapshot.prices
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["provider", "model_pattern", "effective_from", "min_prompt_tokens"]
        )
        .returning(ModelPrice.id)
    )
    result = await session.execute(statement)
    return len(result.all())
