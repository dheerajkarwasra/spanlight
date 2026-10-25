"""Seed price snapshot (USD per million tokens, standard first-party API rates).

Only models with published prices we are confident in are listed; any other
model yields a NULL cost, never zero. Rates exclude regional modifiers and
taxes. A snapshot also carries what a provider bills next to the token rate:
the service-tier multipliers and the server-tool fees below.

Anthropic rows carry the prompt-cache write rates by lifetime: 1.25x the input
rate for a 5-minute cache and 2x for a 1-hour cache. A model that bills longer
prompts at a higher rate gets one extra row per tier, `min_prompt_tokens` being
the smallest prompt the row prices (Claude Haiku 5.5 over 100,000 tokens,
Gemini Pro over 200,000, OpenAI's 1.05M-context models over 272,000). OpenAI
bills cache writes from GPT-5.6 on, at 1.25x the input rate, with one 30-minute
lifetime: that rate sits in the 5-minute column, the provider's default
lifetime, and OpenAI never reports a 1-hour split. Earlier OpenAI models bill
no writes, so their rows have no write rate. Gemini (`google`) bills cache storage per hour, not
per write, so its rows have no write rate either; that storage is not modelled.
Gemini output tokens must include thinking tokens (`thoughts_token_count`): an
integration that reports only `candidates_token_count` under-prices. Gemini
3.6 Flash and 3.8 Flash double their rates on 2027-01-01, so a `2027-01-01`
snapshot is due then.

Tier multipliers scale the token cost by (provider, tier): batch at half price
everywhere, OpenAI flex at half, Anthropic priority at the standard rate and
flagged `commitment_billed` because it is billed as a commitment outside the
per-token rate. OpenAI priority (Fast) has no factor: the page publishes
per-model Fast rates, not a multiplier, so such a span is priced at the
standard rate and flagged as an unmodelled modifier (`docs/api-deviations.md`).
Server-tool fees are USD per 1,000 requests of a provider's built-in tool
(web search today) and are added to the token cost.

`SEED_PRICES` is the current snapshot, in force from `EFFECTIVE_FROM` under
`PRICING_VERSION`. A span is priced by the snapshot in force when it started,
and ingestion accepts spans up to 90 days old, so `HISTORICAL_SNAPSHOTS` keeps
each earlier snapshot exactly as it was released: a fresh install then prices
an older span as an upgraded one does. A released snapshot is never edited; a
corrected or new rate goes into a new snapshot with a new date, and a stored
cost is never re-priced. The `2026-10-16` snapshot corrects the Claude Sonnet
5.5 cache-read rate ($0.10, not $0.20): a Sonnet 5.5 span priced under
`2026-10-15` keeps its stored cost, whose cache-read part is twice the billed
rate. The `2026-10-19` snapshot adds the GPT-6, GPT-5.x, o-series and legacy
OpenAI models and the dated `gpt-4o-2024-05-13` row, which earlier snapshots
priced at the `gpt-4o` rate (half what OpenAI bills).

The docs site builds its Prices page from the current snapshot only
(`docs-site/scripts/lib/prices.mjs`): the `_price(...)` calls inside the
`SEED_PRICES` block, the `TIER_MULTIPLIERS` and `COMMITMENT_BILLED` literals
and the `_fee(...)` calls of `SEED_FEES`. Keep them literal so the parser can
read them; historical snapshots are not rendered.

When refreshing prices, refresh the announced retirement dates in `app.pricing.retirements`
from the providers' deprecation pages too (the `model_retiring` detector reads them).

Sources: https://platform.claude.com/docs/en/about-claude/pricing (and the
owner's copy of the pricing table, 2026-10-15), https://ai.google.dev/gemini-api/docs/pricing
(checked 2026-10-15) and https://developers.openai.com/api/docs/pricing with the
model pages and the prompt-caching guide (checked 2026-10-19).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.pricing.cost import PriceBook

PRICING_VERSION = "2026-10-19"
EFFECTIVE_FROM = datetime(2026, 10, 19, tzinfo=UTC)


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
class SeedFee:
    """What a provider bills per 1,000 requests of a server-side tool."""

    provider: str
    tool: str
    usd_per_1k_requests: Decimal


@dataclass(frozen=True)
class PriceSnapshot:
    """One release of the price table: its rows, and when and under what version they apply.

    `tier_multipliers` maps (provider, tier) to the factor applied to the token cost;
    `commitment_billed` names the pairs a provider bills outside the per-token rate; `fees` are
    the server-tool fees released with the snapshot.
    """

    version: str
    effective_from: datetime
    prices: tuple[SeedPrice, ...]
    tier_multipliers: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)
    commitment_billed: frozenset[tuple[str, str]] = frozenset()
    fees: tuple[SeedFee, ...] = ()


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


def _fee(provider: str, tool: str, rate: str) -> SeedFee:
    return SeedFee(provider=provider, tool=tool, usd_per_1k_requests=Decimal(rate))


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
    # Cache read corrected from 0.20 (the 2026-10-15 snapshot) to the published 0.10.
    _price(
        "anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.10", write_5m="2.50", write_1h="4.00"
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
    _price(
        "anthropic",
        "claude-fable-5-1",
        "10.00",
        "50.00",
        "0.25",
        write_5m="12.50",
        write_1h="20.00",
    ),
    _price(
        "anthropic",
        "claude-mythos-5-1",
        "10.00",
        "50.00",
        "0.25",
        write_5m="12.50",
        write_1h="20.00",
    ),
    _price(
        "anthropic", "claude-fable-5", "10.00", "50.00", "1.00", write_5m="12.50", write_1h="20.00"
    ),
    _price(
        "anthropic", "claude-mythos-5", "10.00", "50.00", "1.00", write_5m="12.50", write_1h="20.00"
    ),
    _price(
        "anthropic", "claude-opus-4-1", "15.00", "75.00", "1.50", write_5m="18.75", write_1h="30.00"
    ),
    _price(
        "anthropic", "claude-opus-4", "15.00", "75.00", "1.50", write_5m="18.75", write_1h="30.00"
    ),
    _price(
        "anthropic", "claude-sonnet-4", "3.00", "15.00", "0.30", write_5m="3.75", write_1h="6.00"
    ),
    # Anthropic names this model `claude-3-5-haiku` (`claude-3-5-haiku-20241022`, `-latest`).
    _price(
        "anthropic", "claude-3-5-haiku", "0.80", "4.00", "0.08", write_5m="1.00", write_1h="1.60"
    ),
    # OpenAI. Cached = cached-input rate. GPT-5.6 and later bill a cache write at 1.25x the input
    # rate (one 30-minute lifetime, kept in the 5-minute column); earlier models bill no writes.
    # Models with a 1.05M context window bill prompts over 272K input tokens at 2x the input and
    # cache rates and 1.5x output for the whole request: the second row of those models.
    _price("openai", "gpt-6-astra", "10.00", "50.00", "1.00", write_5m="12.50"),
    _price(
        "openai",
        "gpt-6-astra",
        "20.00",
        "75.00",
        "2.00",
        write_5m="25.00",
        min_prompt_tokens=272_001,
    ),
    _price("openai", "gpt-6.1-sol", "2.00", "10.00", "0.10", write_5m="2.50"),
    _price(
        "openai", "gpt-6.1-sol", "4.00", "15.00", "0.20", write_5m="5.00", min_prompt_tokens=272_001
    ),
    _price("openai", "gpt-6-sol", "2.00", "10.00", "0.20", write_5m="2.50"),
    _price(
        "openai", "gpt-6-sol", "4.00", "15.00", "0.40", write_5m="5.00", min_prompt_tokens=272_001
    ),
    _price("openai", "gpt-6-luna", "0.10", "0.50", "0.01", write_5m="0.125"),
    _price(
        "openai", "gpt-6-luna", "0.20", "0.75", "0.02", write_5m="0.25", min_prompt_tokens=272_001
    ),
    # GPT-5.6 Sol's promotional rate is guaranteed only through 2026-11-21.
    _price("openai", "gpt-5.6-sol", "4.00", "20.00", "0.40", write_5m="5.00"),
    _price(
        "openai",
        "gpt-5.6-sol",
        "8.00",
        "30.00",
        "0.80",
        write_5m="10.00",
        min_prompt_tokens=272_001,
    ),
    _price("openai", "gpt-5.6-terra", "2.00", "12.00", "0.20", write_5m="2.50"),
    _price(
        "openai",
        "gpt-5.6-terra",
        "4.00",
        "18.00",
        "0.40",
        write_5m="5.00",
        min_prompt_tokens=272_001,
    ),
    _price("openai", "gpt-5.6-luna", "0.20", "1.20", "0.02", write_5m="0.25"),
    _price(
        "openai", "gpt-5.6-luna", "0.40", "1.80", "0.04", write_5m="0.50", min_prompt_tokens=272_001
    ),
    _price("openai", "gpt-5.6-cyber", "12.50", "75.00", "1.25", write_5m="15.625"),
    _price("openai", "gpt-5.5", "5.00", "30.00", "0.50"),
    _price("openai", "gpt-5.5", "10.00", "45.00", "1.00", min_prompt_tokens=272_001),
    _price("openai", "gpt-5.5-pro", "30.00", "180.00", None),
    _price("openai", "gpt-5.5-pro", "60.00", "270.00", None, min_prompt_tokens=272_001),
    _price("openai", "gpt-5.4", "2.50", "15.00", "0.25"),
    _price("openai", "gpt-5.4", "5.00", "22.50", "0.50", min_prompt_tokens=272_001),
    _price("openai", "gpt-5.4-pro", "30.00", "180.00", None),
    _price("openai", "gpt-5.4-pro", "60.00", "270.00", None, min_prompt_tokens=272_001),
    _price("openai", "gpt-5.4-mini", "0.75", "4.50", "0.075"),
    _price("openai", "gpt-5.4-nano", "0.20", "1.25", "0.02"),
    _price("openai", "gpt-5.3-codex", "1.75", "14.00", "0.175"),
    _price("openai", "gpt-5.2", "1.75", "14.00", "0.175"),
    _price("openai", "gpt-5.2-pro", "21.00", "168.00", None),
    _price("openai", "gpt-5.1", "1.25", "10.00", "0.125"),
    _price("openai", "gpt-5", "1.25", "10.00", "0.125"),
    _price("openai", "gpt-5-mini", "0.25", "2.00", "0.025"),
    _price("openai", "gpt-5-nano", "0.05", "0.40", "0.005"),
    _price("openai", "gpt-5-pro", "15.00", "120.00", None),
    _price("openai", "chat-latest", "5.00", "30.00", "0.50"),
    _price("openai", "gpt-rosalind-research", "5.00", "25.00", "0.50"),
    _price("openai", "gpt-rosalind-discovery", "5.00", "25.00", "0.50"),
    _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
    _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
    _price("openai", "gpt-4.1-nano", "0.10", "0.40", "0.025"),
    _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
    # The first gpt-4o snapshot bills twice the gpt-4o rate; the longer pattern wins.
    _price("openai", "gpt-4o-2024-05-13", "5.00", "15.00", None),
    _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
    _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
    _price("openai", "o3", "2.00", "8.00", "0.50"),
    _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
    _price("openai", "o3-pro", "20.00", "80.00", None),
    _price("openai", "o1", "15.00", "60.00", "7.50"),
    _price("openai", "o1-pro", "150.00", "600.00", None),
    _price("openai", "gpt-4-turbo-2024-04-09", "10.00", "30.00", None),
    _price("openai", "gpt-4-0613", "30.00", "60.00", None),
    _price("openai", "gpt-3.5-turbo", "0.50", "1.50", None),
    _price("openai", "gpt-3.5-turbo-0125", "0.50", "1.50", None),
    _price("openai", "gpt-3.5-turbo-1106", "1.00", "2.00", None),
    _price("openai", "gpt-3.5-turbo-instruct", "1.50", "2.00", None),
    _price("openai", "davinci-002", "2.00", "2.00", None),
    _price("openai", "babbage-002", "0.40", "0.40", None),
    # Google Gemini, paid tier, text input. Cached = context-cache read rate; cache storage is
    # billed per hour and is not modelled. Prompts over 200K tokens bill at the next tier.
    _price("google", "gemini-2.5-pro", "1.25", "10.00", "0.125"),
    _price("google", "gemini-2.5-pro", "2.50", "15.00", "0.25", min_prompt_tokens=200_001),
    _price("google", "gemini-2.5-flash", "0.30", "2.50", "0.03"),
    _price("google", "gemini-2.5-flash-lite", "0.10", "0.40", "0.01"),
    _price("google", "gemini-3-flash-preview", "0.50", "3.00", "0.05"),
    _price("google", "gemini-3.1-pro-preview", "2.00", "12.00", "0.20"),
    _price("google", "gemini-3.1-pro-preview", "4.00", "18.00", "0.40", min_prompt_tokens=200_001),
    _price("google", "gemini-3.1-flash-lite", "0.25", "1.50", "0.025"),
    _price("google", "gemini-3.5-flash-lite", "0.30", "2.50", "0.03"),
    # Both Flash rates double on 2027-01-01 (next snapshot).
    _price("google", "gemini-3.6-flash", "0.75", "3.75", "0.075"),
    _price("google", "gemini-3.8-flash", "0.75", "3.75", "0.075"),
)

# (provider, tier) -> factor on the token cost. A pair missing here is priced at the standard
# rate; OpenAI priority is missing on purpose (see the module docstring).
TIER_MULTIPLIERS: Mapping[tuple[str, str], Decimal] = MappingProxyType(
    {
        ("anthropic", "batch"): Decimal("0.5"),
        ("anthropic", "priority"): Decimal("1.0"),
        ("openai", "batch"): Decimal("0.5"),
        ("openai", "flex"): Decimal("0.5"),
        ("google", "batch"): Decimal("0.5"),
    }
)
COMMITMENT_BILLED: frozenset[tuple[str, str]] = frozenset({("anthropic", "priority")})

# USD per 1,000 requests of a provider's server-side tool.
SEED_FEES: tuple[SeedFee, ...] = (
    _fee("anthropic", "web_search", "10.00"),
    _fee("openai", "web_search", "10.00"),
)

CURRENT_SNAPSHOT = PriceSnapshot(
    version=PRICING_VERSION,
    effective_from=EFFECTIVE_FROM,
    prices=SEED_PRICES,
    tier_multipliers=TIER_MULTIPLIERS,
    commitment_billed=COMMITMENT_BILLED,
    fees=SEED_FEES,
)


# Released 2026-10-16 (sources checked 2026-10-15), verbatim. Its OpenAI rows predate the GPT-5 and
# GPT-6 families and OpenAI's cache-write rates. Its multipliers, commitment flags and fees are the
# current ones, unchanged in 2026-10-19; a snapshot that changes them must freeze a copy here.
_PRICES_2026_10_16: tuple[SeedPrice, ...] = (
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
    # Cache read corrected from 0.20 (the 2026-10-15 snapshot) to the published 0.10.
    _price(
        "anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.10", write_5m="2.50", write_1h="4.00"
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
    _price(
        "anthropic",
        "claude-fable-5-1",
        "10.00",
        "50.00",
        "0.25",
        write_5m="12.50",
        write_1h="20.00",
    ),
    _price(
        "anthropic",
        "claude-mythos-5-1",
        "10.00",
        "50.00",
        "0.25",
        write_5m="12.50",
        write_1h="20.00",
    ),
    _price(
        "anthropic", "claude-fable-5", "10.00", "50.00", "1.00", write_5m="12.50", write_1h="20.00"
    ),
    _price(
        "anthropic", "claude-mythos-5", "10.00", "50.00", "1.00", write_5m="12.50", write_1h="20.00"
    ),
    _price(
        "anthropic", "claude-opus-4-1", "15.00", "75.00", "1.50", write_5m="18.75", write_1h="30.00"
    ),
    _price(
        "anthropic", "claude-opus-4", "15.00", "75.00", "1.50", write_5m="18.75", write_1h="30.00"
    ),
    _price(
        "anthropic", "claude-sonnet-4", "3.00", "15.00", "0.30", write_5m="3.75", write_1h="6.00"
    ),
    # Anthropic names this model `claude-3-5-haiku` (`claude-3-5-haiku-20241022`, `-latest`).
    _price(
        "anthropic", "claude-3-5-haiku", "0.80", "4.00", "0.08", write_5m="1.00", write_1h="1.60"
    ),
    # OpenAI. Cached = cached-input rate; cache writes are not billed separately.
    _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
    _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
    _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
    _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
    _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
    _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
    # Google Gemini, paid tier, text input. Cached = context-cache read rate; cache storage is
    # billed per hour and is not modelled. Prompts over 200K tokens bill at the next tier.
    _price("google", "gemini-2.5-pro", "1.25", "10.00", "0.125"),
    _price("google", "gemini-2.5-pro", "2.50", "15.00", "0.25", min_prompt_tokens=200_001),
    _price("google", "gemini-2.5-flash", "0.30", "2.50", "0.03"),
    _price("google", "gemini-2.5-flash-lite", "0.10", "0.40", "0.01"),
    _price("google", "gemini-3-flash-preview", "0.50", "3.00", "0.05"),
    _price("google", "gemini-3.1-pro-preview", "2.00", "12.00", "0.20"),
    _price("google", "gemini-3.1-pro-preview", "4.00", "18.00", "0.40", min_prompt_tokens=200_001),
    _price("google", "gemini-3.1-flash-lite", "0.25", "1.50", "0.025"),
    _price("google", "gemini-3.5-flash-lite", "0.30", "2.50", "0.03"),
    # Both Flash rates double on 2027-01-01 (next snapshot).
    _price("google", "gemini-3.6-flash", "0.75", "3.75", "0.075"),
    _price("google", "gemini-3.8-flash", "0.75", "3.75", "0.075"),
)

SNAPSHOT_2026_10_16 = PriceSnapshot(
    version="2026-10-16",
    effective_from=datetime(2026, 10, 16, tzinfo=UTC),
    prices=_PRICES_2026_10_16,
    tier_multipliers=TIER_MULTIPLIERS,
    commitment_billed=COMMITMENT_BILLED,
    fees=SEED_FEES,
)


# Released 2026-10-15 (sources checked that day), verbatim. It introduced the cache-write rates
# and the Haiku 5.5 tier; it stores the Claude Sonnet 5.5 cache read at 0.20, corrected to 0.10
# in the next snapshot. Spans priced under it keep their stored cost.
_PRICES_2026_10_15: tuple[SeedPrice, ...] = (
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
    _price("openai", "gpt-4o", "2.50", "10.00", "1.25"),
    _price("openai", "gpt-4o-mini", "0.15", "0.60", "0.075"),
    _price("openai", "gpt-4.1", "2.00", "8.00", "0.50"),
    _price("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10"),
    _price("openai", "o3-mini", "1.10", "4.40", "0.55"),
    _price("openai", "o4-mini", "1.10", "4.40", "0.275"),
)

SNAPSHOT_2026_10_15 = PriceSnapshot(
    version="2026-10-15",
    effective_from=datetime(2026, 10, 15, tzinfo=UTC),
    prices=_PRICES_2026_10_15,
)


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
HISTORICAL_SNAPSHOTS: tuple[PriceSnapshot, ...] = (
    SNAPSHOT_2026_10_01,
    SNAPSHOT_2026_10_15,
    SNAPSHOT_2026_10_16,
)
ALL_SNAPSHOTS: tuple[PriceSnapshot, ...] = (*HISTORICAL_SNAPSHOTS, CURRENT_SNAPSHOT)


def seed_price_book() -> "PriceBook":
    """The current snapshot as a `PriceBook`, for code that prices without a database.

    The same rows `sync_seed_prices` writes for the current snapshot, with its fees; historical
    snapshots are left out, so a span older than `EFFECTIVE_FROM` is unpriced here.
    """
    from app.pricing.cost import PriceBook  # noqa: PLC0415 - cost imports this module
    from app.pricing.fees import FeeBook, ServerToolFee  # noqa: PLC0415
    from app.pricing.price_row import Price  # noqa: PLC0415

    prices = [
        Price(
            provider=seed.provider,
            model_pattern=seed.model_pattern,
            input_per_mtok=seed.input_per_mtok,
            output_per_mtok=seed.output_per_mtok,
            cached_input_per_mtok=seed.cached_input_per_mtok,
            effective_from=EFFECTIVE_FROM,
            version=PRICING_VERSION,
            cache_write_5m_per_mtok=seed.cache_write_5m_per_mtok,
            cache_write_1h_per_mtok=seed.cache_write_1h_per_mtok,
            min_prompt_tokens=seed.min_prompt_tokens,
        )
        for seed in SEED_PRICES
    ]
    fees = FeeBook(
        ServerToolFee(
            provider=fee.provider,
            tool=fee.tool,
            usd_per_1k_requests=fee.usd_per_1k_requests,
            effective_from=EFFECTIVE_FROM,
            version=PRICING_VERSION,
        )
        for fee in SEED_FEES
    )
    return PriceBook(prices, fees=fees)
