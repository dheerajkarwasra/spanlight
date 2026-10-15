"""OTLP usage attributes under both naming generations of the GenAI conventions.

LiteLLM 1.103 (with `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`) and current
OpenTelemetry instrumentations name cache usage `gen_ai.usage.cache_read.input_tokens`; older
instrumentations use `gen_ai.usage.cache_read_input_tokens`. Both must reach `cached_tokens`, or a
cached call is priced as if every input token were uncached. Cache writes follow the same pair
(`gen_ai.usage.cache_creation.input_tokens`, then `gen_ai.usage.cache_creation_input_tokens`) into
`cache_write_tokens`; OTel carries no TTL split, so `cache_write_1h_tokens` is never set.
"""

from typing import Any

import pytest

from app.ingest.otlp import OtlpSpan, to_native_span

# Usage as LiteLLM exported it for a real claude-haiku-4-5 cache read in the 2026-10-14 pilot:
# input_tokens already includes the cached part (19 uncached + 6,402 read).
LITELLM_CACHE_READ: dict[str, Any] = {
    "gen_ai.provider.name": "anthropic",
    "gen_ai.request.model": "claude-haiku-4-5",
    "gen_ai.response.model": "claude-haiku-4-5-20251001",
    "gen_ai.usage.input_tokens": 6421,
    "gen_ai.usage.output_tokens": 10,
    "gen_ai.usage.cache_read.input_tokens": 6402,
    "gen_ai.usage.cache_creation.input_tokens": 0,
}


def _span(attributes: dict[str, Any]) -> OtlpSpan:
    return OtlpSpan(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="litellm_request",
        kind=3,
        start_unix_nano=1_760_000_000_000_000_000,
        end_unix_nano=1_760_000_000_500_000_000,
        status_code=1,
        status_message=None,
        attributes=attributes,
        resource={"service.name": "support-bot"},
    )


def test_current_cache_read_name_is_read() -> None:
    usage = to_native_span(_span(LITELLM_CACHE_READ))["usage"]

    assert usage == {
        "input_tokens": 6421,
        "output_tokens": 10,
        "cached_tokens": 6402,
        "cache_write_tokens": 0,
    }


def test_legacy_cache_read_name_still_works() -> None:
    attributes = {**LITELLM_CACHE_READ}
    del attributes["gen_ai.usage.cache_read.input_tokens"]
    attributes["gen_ai.usage.cache_read_input_tokens"] = 6402

    assert to_native_span(_span(attributes))["usage"]["cached_tokens"] == 6402


@pytest.mark.parametrize("current_first", [True, False])
def test_current_name_wins_when_both_are_sent(current_first: bool) -> None:
    pairs = [
        ("gen_ai.usage.cache_read.input_tokens", 6402),
        ("gen_ai.usage.cache_read_input_tokens", 1),
    ]
    attributes = {**LITELLM_CACHE_READ}
    del attributes["gen_ai.usage.cache_read.input_tokens"]
    attributes.update(pairs if current_first else reversed(pairs))

    assert to_native_span(_span(attributes))["usage"]["cached_tokens"] == 6402


# The same model writing a 6,404-token prefix to the cache (19 uncached + 6,404 written).
LITELLM_CACHE_WRITE: dict[str, Any] = {
    **LITELLM_CACHE_READ,
    "gen_ai.usage.input_tokens": 6423,
    "gen_ai.usage.cache_read.input_tokens": 0,
    "gen_ai.usage.cache_creation.input_tokens": 6404,
}


def test_current_cache_write_name_is_read() -> None:
    usage = to_native_span(_span(LITELLM_CACHE_WRITE))["usage"]

    assert usage == {
        "input_tokens": 6423,
        "output_tokens": 10,
        "cached_tokens": 0,
        "cache_write_tokens": 6404,
    }
    assert "cache_write_1h_tokens" not in usage


def test_legacy_cache_write_name_still_works() -> None:
    attributes = {**LITELLM_CACHE_WRITE}
    del attributes["gen_ai.usage.cache_creation.input_tokens"]
    attributes["gen_ai.usage.cache_creation_input_tokens"] = 6404

    assert to_native_span(_span(attributes))["usage"]["cache_write_tokens"] == 6404


@pytest.mark.parametrize("current_first", [True, False])
def test_current_cache_write_name_wins_when_both_are_sent(current_first: bool) -> None:
    pairs = [
        ("gen_ai.usage.cache_creation.input_tokens", 6404),
        ("gen_ai.usage.cache_creation_input_tokens", 1),
    ]
    attributes = {**LITELLM_CACHE_WRITE}
    del attributes["gen_ai.usage.cache_creation.input_tokens"]
    attributes.update(pairs if current_first else reversed(pairs))

    assert to_native_span(_span(attributes))["usage"]["cache_write_tokens"] == 6404


def test_absent_cache_write_stays_unknown() -> None:
    attributes = {**LITELLM_CACHE_READ}
    del attributes["gen_ai.usage.cache_creation.input_tokens"]

    assert to_native_span(_span(attributes))["usage"]["cache_write_tokens"] is None
