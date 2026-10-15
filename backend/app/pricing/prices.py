"""Seed price snapshot (USD per million tokens, standard first-party API rates).

Only models with published prices we are confident in are listed; any other
model yields a NULL cost, never zero. Rates exclude batch discounts, regional
modifiers, server-tool fees and taxes.

Anthropic rows carry the prompt-cache write rates by lifetime: 1.25x the input
rate for a 5-minute cache and 2x for a 1-hour cache. A model that bills longer
prompts at a higher rate gets one extra row per tier, `min_prompt_tokens` being
the smallest prompt the row prices (Claude Haiku 5.5 over 100,000 tokens).
OpenAI does not bill cache writes separately, so its rows have no write rate.

`SEED_PRICES` is the current snapshot, in force from `EFFECTIVE_FROM` under
`PRICING_VERSION`. A span is priced by the snapshot in force when it started,
and ingestion accepts spans up to 90 days old, so `HISTORICAL_SNAPSHOTS` keeps
each earlier snapshot exactly as it was released: a fresh install then prices
an older span as an upgraded one does. A released snapshot is never edited; a
corrected or new rate goes into a new snapshot with a new date.

The docs site builds its Prices page from the `_price(...)` calls inside the
`SEED_PRICES` block only (`docs-site/scripts/lib/prices.mjs`); historical
snapshots are not rendered.

Sources: https://platform.claude.com/docs/en/about-claude/pricing and
https://openai.com/api/pricing (checked 2026-10-15).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

PRICING_VERSION = "2026-10-15"
EFFECTIVE_FROM = datetime(2026, 10, 15, tzinfo=UTC)


@dataclass(frozen=True)
class SeedPrice:
    provider: str
    model_pattern: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None
    cache_write_5m_per_mtok: Decimal | None = None
    cache_write_1h_per_mtok: Decimal | None = None
    min_prompt_tokens: int = 0


@dataclass(frozen=True)
class PriceSnapshot:
    """One release of the price table: its rows, and when and under what version they apply."""

    version: str
    effective_from: datetime
    prices: tuple[SeedPrice, ...]


def _price(
    provider: str,
    model: str,
    input_rate: str,
    output_rate: str,
    cached_rate: str | None,
    *,
    write_5m: str | None = None,
    write_1h: str | None = None,
    min_prompt_tokens: int = 0,
) -> SeedPrice:
    return SeedPrice(
        provider=provider,
        model_pattern=model,
        input_per_mtok=Decimal(input_rate),
        output_per_mtok=Decimal(output_rate),
        cached_input_per_mtok=Decimal(cached_rate) if cached_rate is not None else None,
        cache_write_5m_per_mtok=Decimal(write_5m) if write_5m is not None else None,
        cache_write_1h_per_mtok=Decimal(write_1h) if write_1h is not None else None,
        min_prompt_tokens=min_prompt_tokens,
    )


SEED_PRICES: tuple[SeedPrice, ...] = (
    # Anthropic. Cached = prompt-cache read rate; writes = 1.25x (5 m) and 2x (1 h) the input rate.
    _price(
        "anthropic", "claude-haiku-4-5", "1.00", "5.00", "0.10", write_5m="1.25", write_1h="2.00"
    ),
    _price(
        "anthropic", "claude-sonnet-4-5", "3.00", "15.00", "0.30", write_5m="3.75", write_1h="6.00"
    ),
    _price(
        "anthropic", "claude-sonnet-4-6", "3.00", "15.00", "0.30", write_5m="3.75", write_1h="6.00"
    ),
    _price(
        "anthropic", "claude-opus-4-5", "5.00", "25.00", "0.50", write_5m="6.25", write_1h="10.00"
    ),
    _price(
        "anthropic", "claude-opus-4-6", "5.00", "25.00", "0.50", write_5m="6.25", write_1h="10.00"
    ),
    _price(
        "anthropic", "claude-opus-4-7", "5.00", "25.00", "0.50", write_5m="6.25", write_1h="10.00"
    ),
    _price(
        "anthropic", "claude-opus-4-8", "5.00", "25.00", "0.50", write_5m="6.25", write_1h="10.00"
    ),
    _price(
        "anthropic", "claude-opus-5", "5.00", "25.00", "0.50", write_5m="6.25", write_1h="10.00"
    ),
    _price(
        "anthropic", "claude-opus-5-5", "4.00", "20.00", "0.20", write_5m="5.00", write_1h="8.00"
    ),
    _price(
        "anthropic", "claude-sonnet-5", "2.00", "10.00", "0.20", write_5m="2.50", write_1h="4.00"
    ),
    _price(
        "anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.20", write_5m="2.50", write_1h="4.00"
    ),
    _price(
        "anthropic", "claude-haiku-5-5", "0.10", "0.50", "0.01", write_5m="0.125", write_1h="0.20"
    ),
    # Prompts over 100K tokens bill the whole prompt at the next tier.
    _price(
        "anthropic",
        "claude-haiku-5-5",
        "0.50",
        "2.50",
        "0.05",
        write_5m="0.625",
        write_1h="1.00",
        min_prompt_tokens=100_001,
    ),
    # OpenAI. Cached = cached-input rate; cache writes are not billed separately.
    _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
    _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
    _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
    _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
    _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
    _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
)

CURRENT_SNAPSHOT = PriceSnapshot(PRICING_VERSION, EFFECTIVE_FROM, SEED_PRICES)


# Released 2026-10-01 (sources checked that day). No write rates and no tiers existed then: cache
# writes were priced at the input rate and the Haiku 5.5 tier over 100K prompt tokens was not
# modelled.
SNAPSHOT_2026_10_01 = PriceSnapshot(
    version="2026-10-01",
    effective_from=datetime(2025, 1, 1, tzinfo=UTC),
    prices=(
        _price("anthropic", "claude-haiku-4-5", "1.00", "5.00", "0.10"),
        _price("anthropic", "claude-sonnet-4-5", "3.00", "15.00", "0.30"),
        _price("anthropic", "claude-sonnet-4-6", "3.00", "15.00", "0.30"),
        _price("anthropic", "claude-opus-4-5", "5.00", "25.00", "0.50"),
        _price("anthropic", "claude-opus-4-6", "5.00", "25.00", "0.50"),
        _price("anthropic", "claude-opus-4-7", "5.00", "25.00", "0.50"),
        _price("anthropic", "claude-opus-4-8", "5.00", "25.00", "0.50"),
        _price("anthropic", "claude-opus-5", "5.00", "25.00", "0.50"),
        _price("anthropic", "claude-opus-5-5", "4.00", "20.00", "0.20"),
        _price("anthropic", "claude-sonnet-5", "2.00", "10.00", "0.20"),
        _price("anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.20"),
        _price("anthropic", "claude-haiku-5-5", "0.10", "0.50", "0.01"),
        _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
        _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
        _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
        _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
        _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
        _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
    ),
)

# Oldest first. Every snapshot is written to the price table, so a span dated before the current
# snapshot is priced by the one in force when it started.
HISTORICAL_SNAPSHOTS: tuple[PriceSnapshot, ...] = (SNAPSHOT_2026_10_01,)
ALL_SNAPSHOTS: tuple[PriceSnapshot, ...] = (*HISTORICAL_SNAPSHOTS, CURRENT_SNAPSHOT)
