"""A playground run prices the cache writes its provider reported, and shows them (pure).

The usage is the 2026-10-14 pilot's real Claude Haiku 4.5 call (`tests/gateway/
test_cache_write_usage.py`): 19 uncached input tokens, 6,404 written with the 1-hour TTL, 12
output tokens, billed $0.012887 by Anthropic. Priced as plain input it would be $0.006483.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.api.schemas.playground import PlaygroundResultOut, UsageOut
from app.gateway.context import GatewayResult
from app.playground.output import result_of
from app.pricing.cost import Price, PriceBook
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SEED_PRICES
from tests.gateway_support import PILOT_COST, PILOT_NATIVE

AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


def _seed_book() -> PriceBook:
    return PriceBook(
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
    )


def _pilot_result() -> GatewayResult:
    return GatewayResult(
        status=200,
        headers={},
        body=b"{}",
        stream=None,
        trace_id="a" * 32,
        span_id="b" * 16,
        model="claude-haiku-4-5-20251001",
        usage=PILOT_NATIVE,
        output={"role": "assistant", "content": [{"type": "text", "text": "Hello!"}]},
    )


def test_playground_cost_includes_the_1h_cache_write() -> None:
    result = result_of(
        _pilot_result(),
        surface="messages",
        provider="anthropic",
        model="claude-haiku-4-5",
        prices=_seed_book(),
        latency_ms=812.0,
        at=AT,
    )

    assert result.error is None and result.output == "Hello!"
    assert result.cost_usd == PILOT_COST
    assert result.cost_usd != Decimal("0.00648300")  # what plain-input pricing would give


def test_playground_response_carries_the_write_fields() -> None:
    result = result_of(
        _pilot_result(),
        surface="messages",
        provider="anthropic",
        model="claude-haiku-4-5",
        prices=_seed_book(),
        latency_ms=812.0,
        at=AT,
    )

    out = PlaygroundResultOut.of(result)

    assert out.usage == UsageOut(
        input_tokens=6423,
        output_tokens=12,
        cached_tokens=0,
        cache_write_tokens=6404,
        cache_write_1h_tokens=6404,
    )
    assert out.model_dump()["usage"]["cache_write_1h_tokens"] == 6404
