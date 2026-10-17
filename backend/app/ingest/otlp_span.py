"""One decoded OTLP span onto the native ingestion shape (spec §4).

`otlp.py` decodes both encodings into `OtlpSpan`; this module maps it. The convention registry
(`app.ingest.conventions`) reads the exporter's vocabulary into `MappedFields`; the common fields
(ids, name, OTLP status, times, resource-derived trace fields) are read here. The span then
flows through the same pipeline as SDK data, so the strict native schema never sees a value this
mapping could not fit: `otlp_fit` drops it and counts the drop.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.text import clean_json
from app.db.models.telemetry import SpanKind
from app.ingest.conventions import Convention, MappedFields, MappedUsage, map_fields
from app.ingest.conventions.read import as_text, first
from app.ingest.metadata import MetadataDrops, record_metadata_drops
from app.ingest.otlp_fit import DroppedValues, fit_span
from app.ingest.otlp_metadata import is_metadata_attribute, trace_metadata

_STATUS_BY_CODE = {0: "unset", 1: "ok", 2: "error"}
_SPAN_KIND_CLIENT = 3
# `SpanIn.finish_reason` is `Text64`; a longer raw value is left out rather than reject the span.
_MAX_FINISH_REASON = 64
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")


@dataclass(frozen=True)
class OtlpSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: int
    start_unix_nano: int
    end_unix_nano: int
    status_code: int
    status_message: str | None
    attributes: dict[str, Any]
    resource: dict[str, Any]


def to_native_spans(spans: list[OtlpSpan]) -> tuple[list[dict[str, Any]], DroppedValues]:
    """The batch in the native shape, and how many values had to be dropped to keep its spans."""
    dropped = DroppedValues()
    return [to_native_span(span, dropped) for span in spans], dropped


def to_native_span(span: OtlpSpan, dropped: DroppedValues | None = None) -> dict[str, Any]:
    """Map one OTLP span onto the native ingestion shape (validated later).

    Every string is cleaned of NUL and lone surrogates, which Postgres cannot store. The request
    body is already clean for OTLP/JSON, but a protobuf string may hold NUL, and the JSON text
    of a `gen_ai.*.messages` attribute is only parsed here, where a `\\ud800` escape in it
    becomes a lone surrogate. A value that does not fit its native field is then dropped (or a
    whole double token count made an integer) rather than the span rejected (`otlp_fit`), and
    an invalid metadata entry is dropped and counted (`otlp_metadata`).
    """
    dropped = dropped if dropped is not None else DroppedValues()
    metadata_drops = MetadataDrops()
    native: dict[str, Any] = clean_json(_map_span(span, metadata_drops))
    fit_span(native, dropped)
    dropped.metadata += metadata_drops.total
    record_metadata_drops("otlp", metadata_drops)
    return native


def count_conventions(
    native_spans: Sequence[Mapping[str, Any]], rejected_indexes: Collection[int]
) -> dict[str, int]:
    """Accepted spans per `spanlight.convention`, for the convention counter."""
    counts: dict[str, int] = {}
    for index, native in enumerate(native_spans):
        if index in rejected_indexes:
            continue
        convention = native["attributes"]["spanlight.convention"]
        counts[convention] = counts.get(convention, 0) + 1
    return counts


def _map_span(span: OtlpSpan, metadata_drops: MetadataDrops) -> dict[str, Any]:
    attributes = span.attributes
    resource = span.resource
    convention, fields = map_fields(attributes, client_span=span.kind == _SPAN_KIND_CLIENT)
    service_name = first(resource, "service.name")
    is_root = span.parent_span_id is None
    finish_reason = fields.finish_reason_raw

    return {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name or (str(service_name) if service_name else "span"),
        "kind": fields.kind.value,
        "status": _STATUS_BY_CODE.get(span.status_code, "unset"),
        "status_message": span.status_message or fields.status_message_fallback,
        "start_time": span.start_unix_nano,
        "end_time": span.end_unix_nano,
        "provider": fields.provider,
        "model": fields.model,
        "usage": _usage(fields.usage),
        "time_to_first_token_ms": fields.time_to_first_token_ms,
        "input": fields.input,
        "output": fields.output,
        "finish_reason": (
            finish_reason if finish_reason and len(finish_reason) <= _MAX_FINISH_REASON else None
        ),
        "attributes": _stored_attributes(attributes, service_name, convention, fields),
        "trace": {
            # Only the root span names the trace; service.name is the fallback.
            "name": (fields.trace_name or span.name or as_text(service_name)) if is_root else None,
            "environment": as_text(
                first(resource, "deployment.environment.name", "deployment.environment")
            ),
            "release": as_text(first(resource, "service.version")),
            "user_id": as_text(first(attributes, "user.id") or first(resource, "user.id")),
            "session_id": as_text(first(attributes, "session.id") or first(resource, "session.id")),
            "metadata": trace_metadata(
                attributes, resource, metadata_drops, convention=fields.metadata
            ),
        },
        "dropped_messages": fields.dropped_messages,
    }


def _usage(usage: MappedUsage) -> dict[str, Any]:
    """The native `usage`: the four counts always, tier and web searches only when sent."""
    native: dict[str, Any] = {name: getattr(usage, name) for name in _TOKEN_FIELDS}
    if usage.service_tier is not None:
        native["service_tier"] = usage.service_tier
    if usage.web_search_requests is not None:
        native["server_tool_use"] = {"web_search_requests": usage.web_search_requests}
    return native


def _stored_attributes(
    attributes: Mapping[str, Any], service_name: Any, convention: Convention, fields: MappedFields
) -> dict[str, Any]:
    # Content is stored in `input`/`output`, and metadata on the trace, not once per span.
    stored = {
        key: value
        for key, value in attributes.items()
        if key not in fields.consumed_keys and not is_metadata_attribute(key)
    }
    if service_name is not None:
        stored.setdefault("service.name", service_name)
    stored["spanlight.convention"] = convention.value
    # Only the mapper sets the operation: a client-sent value must not survive a span without one.
    stored.pop("spanlight.operation", None)
    if fields.operation:
        stored["spanlight.operation"] = fields.operation
    if fields.usage.service_tier_raw is not None:
        stored["usage.service_tier_raw"] = fields.usage.service_tier_raw
    if fields.output_tokens_implied:
        stored["usage.output_tokens_implied"] = True
    if _usage_not_exported(fields):
        stored["usage.unreported_reason"] = "not_exported"
    return stored


def _usage_not_exported(fields: MappedFields) -> bool:
    """A model call whose exporter sent no token count at all (spec §6)."""
    return (
        fields.kind == SpanKind.LLM
        and fields.model is not None
        and all(getattr(fields.usage, name) is None for name in _TOKEN_FIELDS)
    )
