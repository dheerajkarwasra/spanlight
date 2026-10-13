"""Cost calculation for LLM spans.

Token convention (shared with the SDKs, see docs/api-deviations.md):
`input_tokens` is the *total* prompt size including cache reads, and
`cached_tokens` is the subset served from the provider's prompt cache. So

    cost = (input - cached) * input_rate + cached * cached_rate + output * output_rate

where `cached_rate` falls back to `input_rate` when no cache-read price is known.
Cache *writes* are billed by providers at a premium we do not model; the SDK
reports them as an attribute only, so they are priced at the input rate here.

An organization's price overrides are loaded next to the seed prices. For the same model the
longest matching pattern wins whatever its source; between equal patterns an override beats the
seed row. An override's `version` is `override:<id>`, so a stored cost says which rate made it.

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
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SEED_PRICES

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

    def matches(self, model: str) -> bool:
        return matches_model(self.model_pattern, model)


@dataclass(frozen=True)
class CostResult:
    cost_usd: Decimal
    pricing_version: str


class PriceBook:
    """An immutable set of prices loaded once per ingestion batch."""

    def __init__(self, prices: Iterable[Price]) -> None:
        self._prices: Sequence[Price] = tuple(prices)

    def find(self, provider: str | None, model: str, at: datetime) -> Price | None:
        """The price for `model`, matched exactly as given (case included), or None.

        Ingestion strips surrounding whitespace before it stores and prices a model; nothing else
        is normalized, so a price and a model-scoped alert, rollup or budget agree on every span.
        The provider is a fixed vocabulary and is still compared lower-cased.
        """
        normalized_provider = provider.strip().lower() if provider else None
        candidates = [
            price
            for price in self._prices
            if price.effective_from <= at
            and (normalized_provider is None or price.provider == normalized_provider)
            and price.matches(model)
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda price: (
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
    ) -> CostResult | None:
        if model is None or input_tokens is None or output_tokens is None:
            return None
        price = self.find(provider, model, at)
        if price is None:
            return None

        cached = min(cached_tokens or 0, input_tokens)
        uncached = input_tokens - cached
        # Without a published cache-read rate, cached tokens bill at the input rate.
        cached_rate = (
            price.cached_input_per_mtok
            if price.cached_input_per_mtok is not None
            else price.input_per_mtok
        )
        total = (
            uncached * price.input_per_mtok
            + cached * cached_rate
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
            )
            for row in overrides
        )
    return PriceBook(prices)


async def sync_seed_prices(session: AsyncSession) -> int:
    """Insert the bundled price snapshot. Existing rows are left untouched.

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
                    "effective_from": EFFECTIVE_FROM,
                    "version": PRICING_VERSION,
                }
                for seed in SEED_PRICES
            ]
        )
        .on_conflict_do_nothing(index_elements=["provider", "model_pattern", "effective_from"])
        .returning(ModelPrice.id)
    )
    result = await session.execute(statement)
    return len(result.all())
