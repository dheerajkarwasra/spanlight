"""The trace-level fields a native span carries, checked and ready to merge into its trace.

Metadata is validated strictly here, not in the schema: `clean_metadata(strict=True)` names the
offending field in the contract's own words (`trace.metadata.<key>: <reason>` or
`trace.metadata: at most 32 keys`), which a pydantic validator would prefix and reorder. A
violation raises `ValueError`; `normalize_span` turns it into a rejection of the span alone.

Metadata values are redacted after the length check, so the 256-character limit applies to the
value as received; a value redaction replaces stays under its key as `[REDACTED]`.
"""

from dataclasses import dataclass, field

from app.core.redact import redact_text
from app.ingest.metadata import clean_metadata
from app.ingest.schemas import SpanIn


@dataclass(frozen=True)
class TraceFields:
    name: str | None
    environment: str | None
    release: str | None
    external_user_id: str | None
    session_id: str | None
    tags: tuple[str, ...]
    metadata: dict[str, str] = field(default_factory=dict, hash=False)


def trace_fields(span: SpanIn) -> TraceFields:
    """The span's trace fields; `ValueError` with the field path when its metadata is invalid."""
    trace = span.trace
    if trace is None:
        return TraceFields(None, None, None, None, None, ())
    metadata, _ = clean_metadata(trace.metadata, strict=True)
    return TraceFields(
        name=trace.name,
        environment=trace.environment,
        release=trace.release,
        external_user_id=trace.user_id,
        session_id=trace.session_id,
        tags=tuple(trace.tags or ()),
        metadata={key: redact_text(value) for key, value in metadata.items()},
    )
