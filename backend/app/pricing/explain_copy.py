"""The sentences a cost explanation carries in `message`. Pure: strings only.

Kept apart from `explain.py` so the arithmetic module stays short and the copy is reviewed in one
place. Every function takes plain values, never the span, so this module imports nothing from
`explain`. The wording of the `no_usage` and `mismatch` sentences is fixed by the Bet 1 plan;
the UI shows these verbatim.
"""

from decimal import Decimal
from typing import Final

# One sentence per `usage.unreported_reason` value, read into the `no_usage` message. Keyed by
# exactly `app.pricing.unreported.UNREPORTED_REASONS` (`tests/pricing/test_unreported.py`); typed
# by `str` so a stored value of any spelling can be looked up.
UNREPORTED_REASON_COPY: Final[dict[str, str]] = {
    "client_disconnected": "the client disconnected",
    "stream_error": "the stream failed",
    "stream_cut_by_fault": "a Lab fault cut the stream",
    "include_usage_not_set": "stream_options.include_usage was not set",
    "no_final_usage": "the provider sent no final usage event",
    "not_exported": "the exporter sent no usage",
}

FEE_ONLY_MESSAGE: Final = "Token cost unknown; only the web search fee is priced."
USAGE_ROLLUP_MESSAGE: Final = (
    "Usage rollup: this span repeats the usage of its child spans and is excluded from totals."
)
IMPLAUSIBLE_USAGE_MESSAGE: Final = (
    "The token counts are over 100,000,000, more than any model call uses, so the cost is unknown."
)
CACHE_HIT_MESSAGE: Final = (
    "Gateway cache hit: the response was served from the cache, so the cost is 0."
)


def no_usage_message(reason: str | None, *, input_missing: bool, fee_priced: bool) -> str:
    """Why the token cost is unknown: the reported reason when there is one."""
    sentence = UNREPORTED_REASON_COPY.get(reason) if reason is not None else None
    if sentence is not None:
        return f"Output tokens were not reported: {sentence}, so the cost is unknown."
    if fee_priced:
        return FEE_ONLY_MESSAGE
    missing = "Input" if input_missing else "Output"
    return f"{missing} tokens were not reported, so the cost is unknown."


def no_price_message(provider: str | None, model: str | None) -> str:
    """Which provider and model the ingest lookup tried."""
    provider_text = provider or "an unknown provider"
    model_text = model or "an unknown model"
    return (
        f"No price matched {provider_text} {model_text} when the span was ingested, "
        "so the cost is unknown."
    )


def missing_fee_message(provider: str | None, version: str | None) -> str:
    """Searches were counted but the snapshot carries no rate for them.

    Without a version (the tokens were not priced either) no snapshot is named.
    """
    subject = f"No fee rate for {provider or 'unknown provider'} web search"
    return f"{subject} in snapshot {version}." if version is not None else f"{subject}."


def version_missing_message(version: str | None) -> str:
    return (
        f"The price row that priced this span (version {version}) is no longer in the price "
        "table; the stored figure is shown as it was priced."
    )


def override_deleted_message(version: str | None) -> str:
    return (
        f"The price override that priced this span ({version}) was deleted; "
        "the stored figure is shown as it was priced."
    )


def mismatch_message(recomputed: Decimal, stored: Decimal | None) -> str:
    stored_text = str(stored) if stored is not None else "nothing"
    return (
        f"Recomputed {recomputed} differs from stored {stored_text}. "
        "This is a bug in Spanlight; report it with the span id."
    )
