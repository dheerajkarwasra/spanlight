"""OTLP usage attributes → the native `usage` object.

Token counts follow the OpenTelemetry GenAI conventions under both naming generations. The
service tier and the web search count have no `gen_ai.usage.*` convention: the tier is read from
the OpenAI instrumentation's names, then Spanlight's own, and the provider's spelling is folded
onto the four stored tiers here, so the strict native schema never sees it. An unknown spelling
leaves the tier unset (the span is priced as standard) and is returned for
`attributes["usage.service_tier_raw"]` rather than rejecting the span.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from app.pricing.tiers import normalize_tier, unknown_tier_spelling

# The current OpenAI convention, the older `gen_ai.openai.*` name, then Spanlight's own name.
_TIER_KEYS = (
    "openai.response.service_tier",
    "gen_ai.openai.response.service_tier",
    "spanlight.usage.service_tier",
)
# No convention counts web searches.
_WEB_SEARCH_KEY = "spanlight.usage.web_search_requests"


def first_value(source: Mapping[str, Any], keys: Iterable[str]) -> Any:
    """The value of the first key present and not an empty string."""
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def map_usage(
    attributes: Mapping[str, Any], provider: str | None
) -> tuple[dict[str, Any], str | None]:
    """The native `usage` of a span, and the tier spelling `normalize_tier` did not know.

    The tier and `server_tool_use` are added only when their attribute was sent, so a span
    without them maps exactly as before. Counts are passed as sent; `otlp_fit` coerces them.
    """

    def first(*keys: str) -> Any:
        return first_value(attributes, keys)

    usage: dict[str, Any] = {
        "input_tokens": first("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
        "output_tokens": first("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
        # Current conventions (and LiteLLM) name it `cache_read.input_tokens`; older
        # instrumentations `cache_read_input_tokens`. Either way input_tokens includes it.
        "cached_tokens": first(
            "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens"
        ),
        # Cache writes follow the same two naming generations. OTel carries no TTL split,
        # so `cache_write_1h_tokens` is never set and every write is priced as 5-minute.
        "cache_write_tokens": first(
            "gen_ai.usage.cache_creation.input_tokens",
            "gen_ai.usage.cache_creation_input_tokens",
        ),
    }
    raw = as_text(first(*_TIER_KEYS))
    tier = normalize_tier(provider, raw)
    if tier is not None:
        usage["service_tier"] = tier
    searches = first(_WEB_SEARCH_KEY)
    if searches is not None:
        usage["server_tool_use"] = {"web_search_requests": searches}
    # Kept trimmed and cut to 64 characters, as the gateway keeps it; a blank spelling is none.
    return usage, unknown_tier_spelling(raw, tier)
