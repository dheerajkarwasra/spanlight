"""The loss reason vocabulary, and the bucket of each ingest rejection. Pure.

`reason_bucket` maps the rejection texts `app.ingest.pipeline` reports to the client onto the
`ingest` reasons, so the losses table stores a small fixed set of values and never the text
itself, which may quote the payload. The checks run in order:

1. The pipeline's own texts, matched exactly or by their fixed prefix: a duplicate in the batch,
   an unprocessable span, and the semantic checks of `app.ingest.normalize` (too old, in the
   future, its own parent, attributes too large, cost too large).
2. Any other text about a `usage.<field>` is `usage`: the cached and cache-write token checks,
   and schema errors on a usage field.
3. Any other `<location>: <message>` text is a schema error, `validation`.
4. Anything else is `other`.
"""

INGEST_REASONS = (
    "validation",
    "usage",
    "too_old",
    "in_future",
    "self_parent",
    "attributes_too_large",
    "cost_too_large",
    "duplicate_in_batch",
    "unprocessable",
    "other",
)
GATEWAY_REASONS = ("overflow", "error", "rejected")
SDK_REASONS = ("dropped", "failed")

# Prefixes of the texts `pipeline` and `normalize` produce, checked before the generic rules.
_PREFIXES = (
    ("duplicate span_id in batch", "duplicate_in_batch"),
    ("span: could not be processed", "unprocessable"),
    ("start_time: older than", "too_old"),
    ("end_time: is in the future", "in_future"),
    ("parent_span_id: a span cannot", "self_parent"),
    ("attributes: exceed", "attributes_too_large"),
    ("usage: the cost", "cost_too_large"),
    ("usage.", "usage"),
)


def reason_bucket(text: str) -> str:
    """The `ingest` loss reason for one rejection text; `other` when it matches no rule."""
    for prefix, bucket in _PREFIXES:
        if text.startswith(prefix):
            return bucket
    location, separator, message = text.partition(": ")
    if separator and location.strip() and message.strip():
        return "validation"
    return "other"
