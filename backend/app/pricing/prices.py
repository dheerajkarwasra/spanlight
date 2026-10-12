"""Seed price snapshot (USD per million tokens, standard first-party API rates).

Only models with published prices we are confident in are listed; any other
model yields a NULL cost, never zero. Rates exclude batch discounts, long-
context surcharges, cache-write premiums, regional modifiers and taxes.

Sources: https://platform.claude.com/docs/en/about-claude/pricing and
https://openai.com/api/pricing (checked 2026-10-01).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

PRICING_VERSION = "2026-10-01"
EFFECTIVE_FROM = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class SeedPrice:
    provider: str
    model_pattern: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None


def _price(
    provider: str, model: str, input_rate: str, output_rate: str, cached_rate: str | None
) -> SeedPrice:
    return SeedPrice(
        provider=provider,
        model_pattern=model,
        input_per_mtok=Decimal(input_rate),
        output_per_mtok=Decimal(output_rate),
        cached_input_per_mtok=Decimal(cached_rate) if cached_rate is not None else None,
    )


SEED_PRICES: tuple[SeedPrice, ...] = (
    # Anthropic. Cached = prompt-cache read rate.
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
    # Prompts over 100K tokens bill $0.50/$2.50 per MTok; that tier is not modelled.
    _price("anthropic", "claude-haiku-5-5", "0.10", "0.50", "0.01"),
    # OpenAI. Cached = cached-input rate.
    _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
    _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
    _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
    _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
    _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
    _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
)
