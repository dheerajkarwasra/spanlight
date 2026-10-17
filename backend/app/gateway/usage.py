"""Reading token usage from a provider's answer or stream, in the native convention.

Pure. The native convention (`Usage`) counts cached tokens inside `input_tokens`. OpenAI already
does; Anthropic reports uncached input, cache reads and cache writes apart, so they are added up.
A usage without an input count is None: the call's tokens and cost are unknown, never 0. One
with an input count but no output count is a `Usage` whose `output_tokens` is None: the prompt
is known (a stream's first event carries it) while the answer's count never came.

The service tier is normalized here (`normalize_tier`), so every `Usage` carries one of the four
stored values or None; a spelling that did not normalize is kept in `service_tier_raw`, cut to
64 characters.
"""

from collections.abc import Mapping
from typing import Any

import structlog

from app.gateway.sse import Usage, as_int, as_str, dict_at
from app.pricing.tiers import normalize_tier, unknown_tier_spelling

logger = structlog.get_logger(__name__)


def _tier(provider: str, raw: str | None) -> tuple[str | None, str | None]:
    """`(stored tier, raw spelling)`; the raw spelling is kept only when it did not normalize,
    trimmed and cut to 64 characters."""
    tier = normalize_tier(provider, raw)
    return tier, unknown_tier_spelling(raw, tier)


def _openai_usage(
    usage: Mapping[str, Any],
    *,
    input_field: str,
    output_field: str,
    details_field: str,
    service_tier: str | None,
    web_search_requests: int | None,
) -> Usage | None:
    """Chat Completions (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details`) or
    Responses (`input_tokens`, `output_tokens`, `input_tokens_details`) usage."""
    tokens_in = as_int(usage.get(input_field))
    if tokens_in is None:
        return None
    tokens_out = as_int(usage.get(output_field))
    cached = as_int(dict_at(usage, details_field).get("cached_tokens"))
    tier, raw = _tier("openai", service_tier)
    return Usage(
        tokens_in,
        tokens_out,
        cached,
        service_tier=tier,
        web_search_requests=web_search_requests,
        service_tier_raw=raw,
    )


def chat_usage(usage: Mapping[str, Any], *, service_tier: str | None = None) -> Usage | None:
    """`service_tier` is the response's top-level field, which chat usage does not carry."""
    return _openai_usage(
        usage,
        input_field="prompt_tokens",
        output_field="completion_tokens",
        details_field="prompt_tokens_details",
        service_tier=service_tier,
        web_search_requests=None,
    )


def responses_usage(
    usage: Mapping[str, Any],
    *,
    service_tier: str | None = None,
    web_search_requests: int | None = None,
) -> Usage | None:
    """`service_tier` is the response's top-level field; `web_search_requests` is the caller's
    count of completed `web_search_call` output items, which the usage block does not carry."""
    return _openai_usage(
        usage,
        input_field="input_tokens",
        output_field="output_tokens",
        details_field="input_tokens_details",
        service_tier=service_tier,
        web_search_requests=web_search_requests,
    )


def completed_web_searches(output: list[Any]) -> int | None:
    """How many `web_search_call` items of a Responses `output` completed; a failed search is
    not billed, so it is not counted. None when the output has no search item at all: no count
    was reported, so the span gets no fee line (as an Anthropic usage without `server_tool_use`).
    """
    searches = [
        item for item in output if isinstance(item, dict) and item.get("type") == "web_search_call"
    ]
    if not searches:
        return None
    return sum(1 for item in searches if item.get("status") == "completed")


def anthropic_usage(usage: Mapping[str, Any]) -> Usage | None:
    """Messages usage; input tokens include the cached ones, read and written.

    `output_tokens` is None when the field is absent: `message_start` without a `message_delta`.
    The tier is `usage.service_tier` and the search count `server_tool_use.web_search_requests`.
    """
    uncached = as_int(usage.get("input_tokens"))
    if uncached is None:
        return None
    cache_read = as_int(usage.get("cache_read_input_tokens"))
    cache_write, cache_write_1h = _cache_writes(usage)
    tier, raw = _tier("anthropic", as_str(usage.get("service_tier")))
    return Usage(
        uncached + (cache_read or 0) + (cache_write or 0),
        as_int(usage.get("output_tokens")),
        cache_read,
        cache_write,
        cache_write_1h,
        service_tier=tier,
        web_search_requests=as_int(dict_at(usage, "server_tool_use").get("web_search_requests")),
        service_tier_raw=raw,
    )


def _cache_writes(usage: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """`(every cache write, the part written with the 1-hour TTL)`.

    `cache_creation_input_tokens` is every cache write; `cache_creation.ephemeral_1h_input_tokens`
    is the part written with the 1-hour TTL. The split is kept only when it fits inside a write
    total: on its own, or larger than the total, ingestion would refuse the whole span, and the
    call's record matters more than a part the provider got wrong.
    """
    cache_write = as_int(usage.get("cache_creation_input_tokens"))
    cache_write_1h = as_int(dict_at(usage, "cache_creation").get("ephemeral_1h_input_tokens"))
    if cache_write_1h is not None and (cache_write is None or cache_write_1h > cache_write):
        logger.warning(
            "gateway.usage_1h_split_dropped",
            cache_write_tokens=cache_write,
            cache_write_1h_tokens=cache_write_1h,
        )
        cache_write_1h = None
    return cache_write, cache_write_1h
