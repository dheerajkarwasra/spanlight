"""The three provider columns of a span, computed at ingest from its cleaned attributes. Pure.

`normalize_span` calls `provider_columns` after `classify_error`, on every path (native, OTLP and
the gateway's recorder), so each column means the same thing whoever sent the span. Every input
to the rate-limit classifier comes from `read_provider_fields`, which returns bounded strings or
`None`, never a raw attribute value.
"""

from collections.abc import Mapping
from typing import Any, TypedDict

from app.db.models import SpanStatus
from app.ingest.error_class import ErrorClass, numeric_status
from app.ingest.provider_attributes import read_provider_fields
from app.ingest.rate_limit_kind import RateLimitKind, classify_rate_limit
from app.ingest.schemas import SpanIn


class ProviderColumns(TypedDict):
    """The three span columns by name, so they unpack into `NormalizedSpan`; None if unknown."""

    provider_error_type: str | None
    provider_error_code: str | None
    rate_limit_kind: RateLimitKind | None


def provider_columns(
    span: SpanIn,
    error_class: ErrorClass | None,
    message: str | None,
    attributes: Mapping[str, Any],
) -> ProviderColumns:
    """The provider columns of a span, from its redacted message and cleaned attributes.

    The error type and code are an error envelope's, so a successful span keeps neither even
    when its attributes carry them; its rate-limit kind is None too (the classifier needs a
    failed class), so a spoofed header on a success never makes it a refusal.
    """
    if span.status != SpanStatus.ERROR:
        return ProviderColumns(
            provider_error_type=None, provider_error_code=None, rate_limit_kind=None
        )
    fields = read_provider_fields(attributes)
    kind = classify_rate_limit(
        provider=span.provider.lower() if span.provider else None,
        status=numeric_status(attributes),
        error_class=error_class,
        error_type=fields.error_type,
        error_code=fields.error_code,
        message=message,
        headers=fields.ratelimit,
        gateway_error_code=fields.gateway_error_code,
    )
    return ProviderColumns(
        provider_error_type=fields.error_type,
        provider_error_code=fields.error_code,
        rate_limit_kind=kind,
    )
