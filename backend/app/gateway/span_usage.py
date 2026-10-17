"""The native `usage` of a gateway span, from what the provider reported. Pure.

A cache hit was not billed, so its usage is all zeros; an answer whose usage is unknown has
none. An output count the provider never sent stays None (the span is then unpriced, never
cheap); the tier and the web search count are sent only when known.
"""

from typing import Any

from app.gateway.context import GatewayResult
from app.gateway.sse import Usage


def span_usage(result: GatewayResult) -> dict[str, Any] | None:
    """The span's `usage`: zeros for a cache hit, None when the provider reported none."""
    if result.cache == "hit":
        # Nothing was billed: the answer came from the cache.
        return usage_dict(
            Usage(
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                cache_write_tokens=0,
                cache_write_1h_tokens=0,
            )
        )
    return usage_dict(result.usage) if result.usage is not None else None


def usage_dict(usage: Usage) -> dict[str, Any]:
    """The native `usage`; an unknown output count stays None, the tier and searches when known."""
    native: dict[str, Any] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cache_write_1h_tokens": usage.cache_write_1h_tokens,
    }
    if usage.service_tier is not None:
        native["service_tier"] = usage.service_tier  # already normalized
    if usage.web_search_requests is not None:
        native["server_tool_use"] = {"web_search_requests": usage.web_search_requests}
    return native
