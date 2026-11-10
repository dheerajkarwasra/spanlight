"""Cost calculation for LLM spans.

The token cost of one price row is computed in `app.pricing.price_row`; `PriceBook.cost` adds
the tier factor and the fee:

    tokens = token_cost(row, usage) * tier_multiplier * modifier_multipliers
    fee = web_search_requests * fee_rate / 1e3
    cost = quantize(tokens + (fee or 0))

The service tier scales the token cost by the factor of the snapshot that priced the row
(`app.pricing.tiers.multipliers_for`): batch halves it, and a tier the snapshot carries no factor
for (OpenAI priority among them) is billed at the standard rate. An override row uses the
snapshot in force at the span's start time; a snapshot released before tiers were modelled
resolves to the oldest tier table (`tiers` module docstring). The modifiers the response
reported (`app.pricing.modifiers`: Anthropic data residency, fast mode) multiply it further, by
the same snapshot's factors; one without a factor counts as 1.

A server-side tool such as web search adds a fee per request from the fee book
(`app.pricing.fees`). The fee is never scaled by the tier. The token cost and the fee are summed
unrounded and quantized once, so `cost_usd` includes the fee and `fee_usd` is reported beside it.
A span without a provider gets no fee, because each provider bills the same tool differently.

The span's provider and each row's are folded through `canonical_provider` before they are
compared, so a span sent as `gemini` or `vertex_ai` finds the `google` rows and an override saved
under an alias matches every spelling of it; the stored provider is never rewritten. Azure is not
an OpenAI alias: Azure bills its own rates and none are seeded, so an Azure span is unpriced.

An organization's price overrides are loaded next to the seed prices; `best_price` says which
row wins. An override's `version` is `override:<id>`, so a stored cost says which rate made it.
The seed prices and fees are cached per process for `SEED_BOOK_TTL_S`; overrides are read on
every load.

A token cost is only produced when the model has a known price at the span's start
time and both input and output token counts are present. Otherwise `cost_usd` is
NULL, and so is `pricing_version`, even when a fee was priced: unknown is never
reported as zero, and a fee alone is never presented as the span's total.
"""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelPrice, PriceOverride
from app.db.models import ServerToolFee as ServerToolFeeRow
from app.pricing.fees import FEE_QUANTUM, FeeBook, ServerToolFee
from app.pricing.price_row import OVERRIDE_VERSION_PREFIX, Price, best_price, token_cost
from app.pricing.provider_alias import canonical_provider
from app.pricing.seed_sync import sync_seed_prices
from app.pricing.tiers import multipliers_for

# `sync_seed_prices` moved to `app.pricing.seed_sync`; it is still exported here for its callers.
__all__ = [
    "OVERRIDE_VERSION_PREFIX",
    "CostResult",
    "Price",
    "PriceBook",
    "load_price_book",
    "sync_seed_prices",
]

# How long one process reuses the seed prices and fees before reading them again.
SEED_BOOK_TTL_S = 60.0
_SEED_BOOK: dict[str, tuple[float, "PriceBook"]] = {}
_COST_QUANTUM = FEE_QUANTUM  # numeric(14, 8): token costs and fees round alike
_PER_THOUSAND = Decimal(1000)
_WEB_SEARCH = "web_search"
# Far above any model's context window: a call claiming more input or output tokens is a client
# bug, and its cost is unknown rather than a figure that would wreck spend, budgets and alerts.
MAX_PRICED_TOKENS = 100_000_000


@dataclass(frozen=True)
class CostResult:
    """What a span costs. `cost_usd` includes `fee_usd`; both are None when unknown.

    `pricing_version` names the price row behind `cost_usd`, so it is None whenever that is.
    """

    cost_usd: Decimal | None
    fee_usd: Decimal | None
    pricing_version: str | None


_MATCHING_MODELS = 1024


class PriceBook:
    """An immutable set of prices and server-tool fees; the seed book is shared by the process."""

    def __init__(self, prices: Iterable[Price], *, fees: FeeBook | None = None) -> None:
        self._prices: Sequence[Price] = tuple(prices)
        self._fees = fees if fees is not None else FeeBook(())
        self._versions = frozenset(price.version for price in self._prices)
        # Rows by canonical provider, so a lookup scans one provider's rows, not every row.
        self._by_provider: dict[str | None, list[Price]] = {}
        for price in self._prices:
            self._by_provider.setdefault(canonical_provider(price.provider), []).append(price)
        # The rows whose pattern matches a model, by provider and model: a batch prices the same
        # few models on every span, and matching each of a provider's rows was a quarter of the
        # CPU of preparing a span.
        self._matching: dict[tuple[str | None, str], tuple[Price, ...]] = {}

    def find(
        self, provider: str | None, model: str, at: datetime, prompt_tokens: int = 0
    ) -> Price | None:
        """The price for `model`, matched exactly as given (case included), or None.

        Ingestion strips surrounding whitespace before it stores and prices a model; nothing else
        is normalized, so a price and a model-scoped alert, rollup or budget agree on every span.
        Both the span's provider and each row's go through `canonical_provider`, so aliases match
        on either side; no provider matches every row. `prompt_tokens` picks the tier: the row
        with the highest `min_prompt_tokens` not above it.
        """
        canonical = canonical_provider(provider)
        # Every row in the bucket already has the span's canonical provider: `best_price` gets
        # no provider, so it does not fold each row's again. The candidates are the same.
        rows = self._matching.get((canonical, model))
        if rows is None:
            bucket = self._prices if canonical is None else self._by_provider.get(canonical, ())
            rows = tuple(price for price in bucket if price.matches(model))
            # Model names come from clients: the book is shared by the process, so it forgets
            # them all rather than grow with every name ever sent.
            if len(self._matching) >= _MATCHING_MODELS:
                self._matching.clear()
            self._matching[(canonical, model)] = rows
        return best_price(rows, None, model, at, prompt_tokens)

    def find_by_version(
        self, version: str, provider: str | None, model: str, at: datetime, prompt_tokens: int = 0
    ) -> Price | None:
        """As `find`, among the rows of `version` only: the row that priced a stored cost."""
        rows = [price for price in self._prices if price.version == version]
        return best_price(rows, provider, model, at, prompt_tokens)

    @property
    def fees(self) -> FeeBook:
        """The server-tool fees loaded with the prices, for the cost explanation."""
        return self._fees

    def has_version(self, version: str) -> bool:
        """Whether any loaded row, seed or override, carries `version`."""
        return version in self._versions

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
        service_tier: str | None = None,
        web_search_requests: int | None = None,
        modifiers: Iterable[str] = (),
    ) -> CostResult | None:
        """The span's cost and fee, or None when neither can be priced.

        Token counts over `MAX_PRICED_TOKENS` leave the token cost unknown, as a missing price does.

        The fee is priced before the token cost, so a span whose tokens cannot be priced still
        reports a known fee, with `cost_usd` and `pricing_version` None.
        """
        fee = self._web_search_fee(provider, at, web_search_requests)
        fee_usd = None if fee is None else _quantize(fee)
        unpriced = None if fee_usd is None else CostResult(None, fee_usd, None)
        if model is None or input_tokens is None or output_tokens is None:
            return unpriced
        if max(input_tokens, output_tokens) > MAX_PRICED_TOKENS:
            return unpriced
        price = self.find(provider, model, at, prompt_tokens=input_tokens)
        if price is None:
            return unpriced

        tokens = token_cost(
            price,
            input_tokens,
            output_tokens,
            cached_tokens,
            cache_write_tokens,
            cache_write_1h_tokens,
        )
        multiplier = multipliers_for(price.version, at).factor(provider, service_tier, modifiers)
        total = tokens * multiplier + (fee if fee is not None else Decimal(0))
        return CostResult(_quantize(total), fee_usd, price.version)

    def _web_search_fee(
        self, provider: str | None, at: datetime, requests: int | None
    ) -> Decimal | None:
        """The unrounded web search fee, or None when the count or the rate is unknown.

        `fees.fee_cost` rounds the same product; here it stays unrounded so the sum is rounded once.
        """
        if requests is None:
            return None
        rate = self._fees.find(provider, _WEB_SEARCH, at)
        if rate is None:
            return None
        return requests * rate.usd_per_1k_requests / _PER_THOUSAND


def _quantize(amount: Decimal) -> Decimal:
    """Eight decimals half-up, as `numeric(14, 8)` stores it and as `fees.fee_cost` rounds."""
    return amount.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


async def load_price_book(
    session: AsyncSession,
    org_id: UUID | None = None,
    *,
    overrides: Sequence[PriceOverride] | None = None,
) -> PriceBook:
    """The seed prices and fees, plus the overrides of `org_id` when one is given.

    The seed part is shared by the process for `SEED_BOOK_TTL_S` (`_seed_book`); the
    organization's override rows are read on every call, one query, so an override is used from
    the next batch on and never reaches another organization's book.
    A caller that already read overrides in its current query can supply those rows instead;
    an empty sequence means the query found none, not a request to read again.
    """
    seed = await _seed_book(session)
    if org_id is None:
        return seed
    if overrides is None:
        overrides = (
            await session.scalars(select(PriceOverride).where(PriceOverride.org_id == org_id))
        ).all()
    prices = [_override_price(row) for row in overrides if row.org_id == org_id]
    if not prices:
        return seed
    return PriceBook([*seed._prices, *prices], fees=seed.fees)


async def _seed_book(session: AsyncSession) -> PriceBook:
    """The seed prices and fees, read at most once per `SEED_BOOK_TTL_S` in this process.

    Their rows change only when `sync_seed_prices` inserts a release's snapshots (startup, the
    worker, `spanlight sync-prices`), so a process that started on an older release sees them
    within the TTL. Two queries on a miss; concurrent misses each read, and the last one is kept.
    """
    cached = _SEED_BOOK.get("book")
    if cached is not None and time.monotonic() < cached[0]:
        return cached[1]
    rows = (await session.scalars(select(ModelPrice))).all()
    book = PriceBook((_seed_price(row) for row in rows), fees=await _load_fees(session))
    _SEED_BOOK["book"] = (time.monotonic() + SEED_BOOK_TTL_S, book)
    return book


def _seed_price(row: ModelPrice) -> Price:
    return Price(
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


def _override_price(row: PriceOverride) -> Price:
    """An organization's row; its version names it, so a stored cost says which rate made it."""
    return Price(
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


async def _load_fees(session: AsyncSession) -> FeeBook:
    rows = (await session.scalars(select(ServerToolFeeRow))).all()
    return FeeBook(
        ServerToolFee(
            provider=row.provider,
            tool=row.tool,
            usd_per_1k_requests=row.usd_per_1k_requests,
            effective_from=row.effective_from,
            version=row.version,
        )
        for row in rows
    )
