"""Token usage of a span: the counts, the service tier and server-tool use.

:class:`SpanUsage` is a mixin of :class:`~spanlight.Span`; it holds the usage
fields, the setters that fill them and the ``usage`` object of the payload.
"""

from __future__ import annotations

import logging

from ._serialize import JsonValue
from ._tiers import MAX_RAW_TIER_LENGTH, ServiceTier, normalize_tier

logger = logging.getLogger("spanlight")


class SpanUsage:
    """Usage fields and setters of a span; see :meth:`set_usage`."""

    provider: str | None
    attributes: dict[str, JsonValue]

    def _init_usage(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cached_tokens: int | None = None
        self.cache_write_tokens: int | None = None
        self.cache_write_1h_tokens: int | None = None
        self.service_tier: ServiceTier | None = None
        self.web_search_requests: int | None = None

    @property
    def _mutable(self) -> bool:
        """Whether setters still apply; defined by the span."""
        raise NotImplementedError

    def set_usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
        service_tier: str | None = None,
        web_search_requests: int | None = None,
    ) -> None:
        """Record token usage. ``None`` leaves the existing value unchanged.

        ``input_tokens`` is the *total* prompt size: uncached tokens plus
        prompt-cache reads plus prompt-cache writes. ``cached_tokens`` is the
        part served from the cache (reads); ``cache_write_tokens`` is the part
        written to it, and ``cache_write_1h_tokens`` the subset of those writes
        made with a 1-hour TTL (the rest are 5-minute writes). Leave the write
        fields unset when the provider reports none.

        ``service_tier`` is the provider's spelling (``"batch"``,
        ``"priority_on_demand"``, ``"flex"``, ...); it is normalized against
        the provider set by :meth:`set_model`, so set the model first. A
        spelling the SDK does not know is kept (its first 64 characters) in
        the ``usage.service_tier_raw`` attribute and the span is priced as
        standard. ``web_search_requests`` is the number of server-side web
        searches the call made; the provider wrappers read it from the
        response.
        """
        if not self._mutable:
            return
        if input_tokens is not None:
            self.input_tokens = int(input_tokens)
        if output_tokens is not None:
            self.output_tokens = int(output_tokens)
        if cached_tokens is not None:
            self.cached_tokens = int(cached_tokens)
        if cache_write_tokens is not None:
            self.cache_write_tokens = int(cache_write_tokens)
        if cache_write_1h_tokens is not None:
            self.cache_write_1h_tokens = int(cache_write_1h_tokens)
        if service_tier is not None:
            self._set_service_tier(str(service_tier))
        if web_search_requests is not None:
            self.web_search_requests = int(web_search_requests)

    def _set_service_tier(self, raw: str) -> None:
        if not raw.strip():
            return
        tier = normalize_tier(self.provider, raw)
        if tier is None:
            logger.debug("Unknown service tier %r for provider %r", raw, self.provider)
            self.attributes["usage.service_tier_raw"] = raw[:MAX_RAW_TIER_LENGTH]
        else:
            self.service_tier = tier
            # A known tier supersedes an unknown spelling recorded earlier.
            self.attributes.pop("usage.service_tier_raw", None)

    def clear_output_tokens(self) -> None:
        """Forget the output token count, even after one was recorded.

        A stream that ends before the provider's final usage event has no
        trustworthy output count; the wrappers call this so the span is
        exported with ``output_tokens`` absent (unknown) rather than a guess.
        """
        if self._mutable:
            self.output_tokens = None

    def _usage_payload(self) -> dict[str, JsonValue] | None:
        """The ``usage`` object of the span schema, or ``None`` when nothing was recorded.

        Every field is sent only when set, so payloads for servers that predate
        cache-write pricing, service tiers or server-tool counts stay unchanged.
        """
        counts = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_write_1h_tokens": self.cache_write_1h_tokens,
        }
        usage: dict[str, JsonValue] = {k: v for k, v in counts.items() if v is not None}
        if self.service_tier is not None:
            usage["service_tier"] = self.service_tier
        if self.web_search_requests is not None:
            usage["server_tool_use"] = {"web_search_requests": self.web_search_requests}
        return usage or None
