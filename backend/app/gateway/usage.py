"""Reading token usage from a provider's answer or stream, in the native convention.

Pure. The native convention (`Usage`) counts cached tokens inside `input_tokens`. OpenAI already
does; Anthropic reports uncached input, cache reads and cache writes apart, so they are added up.
A usage without both token counts is None: the call's tokens and cost are unknown, never 0.
"""

from collections.abc import Mapping
from typing import Any

import structlog

from app.gateway.sse import Usage, as_int, dict_at

logger = structlog.get_logger(__name__)


def _openai_usage(
    usage: Mapping[str, Any], *, input_field: str, output_field: str, details_field: str
) -> Usage | None:
    """Chat Completions (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details`) or
    Responses (`input_tokens`, `output_tokens`, `input_tokens_details`) usage."""
    tokens_in = as_int(usage.get(input_field))
    tokens_out = as_int(usage.get(output_field))
    if tokens_in is None or tokens_out is None:
        return None
    cached = as_int(dict_at(usage, details_field).get("cached_tokens"))
    return Usage(tokens_in, tokens_out, cached)


def chat_usage(usage: Mapping[str, Any]) -> Usage | None:
    return _openai_usage(
        usage,
        input_field="prompt_tokens",
        output_field="completion_tokens",
        details_field="prompt_tokens_details",
    )


def responses_usage(usage: Mapping[str, Any]) -> Usage | None:
    return _openai_usage(
        usage,
        input_field="input_tokens",
        output_field="output_tokens",
        details_field="input_tokens_details",
    )


def anthropic_usage(usage: Mapping[str, Any]) -> Usage | None:
    """Messages usage; input tokens include the cached ones, read and written.

    `cache_creation_input_tokens` is every cache write; `cache_creation.ephemeral_1h_input_tokens`
    is the part written with the 1-hour TTL. The split is kept only when it fits inside a write
    total: on its own, or larger than the total, ingestion would refuse the whole span, and the
    call's record matters more than a part the provider got wrong.
    """
    uncached = as_int(usage.get("input_tokens"))
    output_tokens = as_int(usage.get("output_tokens"))
    if uncached is None or output_tokens is None:
        return None
    cache_read = as_int(usage.get("cache_read_input_tokens"))
    cache_write = as_int(usage.get("cache_creation_input_tokens"))
    cache_write_1h = as_int(dict_at(usage, "cache_creation").get("ephemeral_1h_input_tokens"))
    if cache_write_1h is not None and (cache_write is None or cache_write_1h > cache_write):
        logger.warning(
            "gateway.usage_1h_split_dropped",
            cache_write_tokens=cache_write,
            cache_write_1h_tokens=cache_write_1h,
        )
        cache_write_1h = None
    total_input = uncached + (cache_read or 0) + (cache_write or 0)
    return Usage(total_input, output_tokens, cache_read, cache_write, cache_write_1h)
