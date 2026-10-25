"""How an Anthropic ``Message`` maps onto a span: usage, id and stop reason.

Anthropic reports ``input_tokens`` *excluding* prompt-cache reads and writes;
:func:`usage_fields` converts it to Spanlight's meaning (the total prompt).
"""

from __future__ import annotations

import logging
from typing import Any

from .._span import Span
from ._common import as_int, get_field

logger = logging.getLogger("spanlight")

PROVIDER = "anthropic"


def record_usage(span: Span, usage: Any) -> None:
    fields = usage_fields(usage)
    if fields is not None:
        span.set_usage(**fields)
    # Where and at which speed the call ran, as billed: the server prices data residency and
    # fast mode from these, in place of the request-side ``usage.pricing_modifier_unknown``.
    for name in ("inference_geo", "speed"):
        value = get_field(usage, name)
        if isinstance(value, str):
            span.set_attribute(f"usage.{name}", value[:64])


def usage_fields(usage: Any) -> dict[str, Any] | None:
    """Anthropic ``usage`` as :meth:`Span.set_usage` keywords, or ``None`` when absent.

    ``service_tier`` is the provider's raw spelling; ``set_usage`` normalizes it.
    """
    if usage is None:
        return None
    uncached = as_int(get_field(usage, "input_tokens"))
    cache_read = as_int(get_field(usage, "cache_read_input_tokens"))
    cache_write = as_int(get_field(usage, "cache_creation_input_tokens"))
    cache_write_1h = as_int(get_field(usage, "cache_creation", "ephemeral_1h_input_tokens"))
    total_input = None
    if uncached is not None:
        total_input = uncached + (cache_read or 0) + (cache_write or 0)
    if cache_write_1h is not None and (cache_write is None or cache_write_1h > cache_write):
        # The server rejects the whole span for an inconsistent split; keep the
        # total and drop the part rather than lose the span.
        logger.debug(
            "Ignoring Anthropic 1h cache-write count %r exceeding total %r",
            cache_write_1h,
            cache_write,
        )
        cache_write_1h = None
    service_tier = get_field(usage, "service_tier")
    return {
        "input_tokens": total_input,
        "output_tokens": as_int(get_field(usage, "output_tokens")),
        "cached_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cache_write_1h_tokens": cache_write_1h,
        "service_tier": service_tier if isinstance(service_tier, str) else None,
        "web_search_requests": as_int(get_field(usage, "server_tool_use", "web_search_requests")),
    }


def record_message_metadata(span: Span, message_id: Any, stop_reason: Any) -> None:
    if isinstance(message_id, str):
        span.set_attribute("response.id", message_id)
    if isinstance(stop_reason, str):
        span.set_attribute("response.stop_reason", stop_reason)
        span.set_finish_reason(stop_reason)
