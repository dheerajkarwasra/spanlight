"""OTLP/HTTP trace export → native span format.

Both encodings (protobuf and JSON) are first decoded into `OtlpSpan`, then a
single mapping turns OpenTelemetry GenAI semantic-convention attributes into
the native `SpanIn` shape, so they flow through the same pipeline as SDK data.
"""

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

from app.core.text import clean_json, storable_double
from app.ingest.otlp_fit import DroppedValues, fit_span
from app.ingest.otlp_values import (
    OtlpDecodeError,
    json_attributes,
    json_id,
    json_int,
    member,
    member_dict,
    member_list,
    parse_content,
)

_STATUS_BY_CODE = {0: "unset", 1: "ok", 2: "error"}
_SPAN_KIND_CLIENT = 3

_CONTENT_INPUT_KEYS = ("gen_ai.input.messages", "gen_ai.prompt")
_CONTENT_OUTPUT_KEYS = ("gen_ai.output.messages", "gen_ai.completion")
_CONTENT_KEYS = frozenset(_CONTENT_INPUT_KEYS + _CONTENT_OUTPUT_KEYS)


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


# --- protobuf -----------------------------------------------------------------


def decode_protobuf(body: bytes) -> list[OtlpSpan]:
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except DecodeError as exc:
        raise OtlpDecodeError("body is not a valid ExportTraceServiceRequest") from exc

    spans: list[OtlpSpan] = []
    for resource_spans in request.resource_spans:
        resource = _proto_attributes(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                spans.append(
                    OtlpSpan(
                        trace_id=span.trace_id.hex(),
                        span_id=span.span_id.hex(),
                        parent_span_id=span.parent_span_id.hex() or None,
                        name=span.name,
                        kind=span.kind,
                        start_unix_nano=span.start_time_unix_nano,
                        end_unix_nano=span.end_time_unix_nano,
                        status_code=span.status.code,
                        status_message=span.status.message or None,
                        attributes=_proto_attributes(span.attributes),
                        resource=resource,
                    )
                )
    return spans


def _proto_attributes(entries: Iterable[KeyValue]) -> dict[str, Any]:
    return {entry.key: _proto_value(entry.value) for entry in entries}


def _proto_value(value: AnyValue) -> Any:
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return storable_double(value.double_value)
    if kind == "array_value":
        return [_proto_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return _proto_attributes(value.kvlist_value.values)
    if kind == "bytes_value":
        return base64.b64encode(value.bytes_value).decode()
    return None


def encode_protobuf_response(rejected: int, message: str | None) -> bytes:
    response = ExportTraceServiceResponse()
    if rejected:
        response.partial_success.CopyFrom(
            ExportTracePartialSuccess(rejected_spans=rejected, error_message=message or "")
        )
    serialized: bytes = response.SerializeToString()
    return serialized


# --- JSON ---------------------------------------------------------------------


def decode_json(document: Any) -> list[OtlpSpan]:
    """Decode OTLP/JSON (lowerCamelCase keys, hex-encoded ids, int64 as strings)."""
    if not isinstance(document, dict):
        raise OtlpDecodeError("body must be a JSON object")

    spans: list[OtlpSpan] = []
    for resource_spans in member_list(document, "resourceSpans", "resource_spans"):
        resource_obj = member_dict(resource_spans, "resource")
        resource = json_attributes(member_list(resource_obj, "attributes"))
        for scope_spans in member_list(resource_spans, "scopeSpans", "scope_spans"):
            for span in member_list(scope_spans, "spans"):
                spans.append(_decode_json_span(span, resource))
    return spans


def _decode_json_span(span: Any, resource: dict[str, Any]) -> OtlpSpan:
    if not isinstance(span, dict):
        raise OtlpDecodeError("each span must be a JSON object")
    status = member_dict(span, "status")
    parent = json_id(member(span, "parentSpanId", "parent_span_id"), size=8)
    return OtlpSpan(
        trace_id=json_id(member(span, "traceId", "trace_id"), size=16) or "",
        span_id=json_id(member(span, "spanId", "span_id"), size=8) or "",
        parent_span_id=parent or None,
        name=str(member(span, "name") or ""),
        kind=json_int(member(span, "kind"), default=0),
        start_unix_nano=json_int(member(span, "startTimeUnixNano", "start_time_unix_nano")),
        end_unix_nano=json_int(member(span, "endTimeUnixNano", "end_time_unix_nano")),
        status_code=json_int(member(status, "code"), default=0),
        status_message=member(status, "message") or None,
        attributes=json_attributes(member_list(span, "attributes")),
        resource=resource,
    )


def json_response(rejected: int, message: str | None) -> dict[str, Any]:
    if not rejected:
        return {}
    return {"partialSuccess": {"rejectedSpans": str(rejected), "errorMessage": message or ""}}


# --- mapping ------------------------------------------------------------------


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
    whole double token count made an integer) rather than the span rejected (`otlp_fit`).
    """
    native: dict[str, Any] = clean_json(_map_span(span))
    fit_span(native, dropped if dropped is not None else DroppedValues())
    return native


def _map_span(span: OtlpSpan) -> dict[str, Any]:
    attributes = span.attributes
    resource = span.resource

    def first(*keys: str, source: Mapping[str, Any] = attributes) -> Any:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
        return None

    is_llm = any(key.startswith("gen_ai.") for key in attributes)
    service_name = first("service.name", source=resource)
    is_root = span.parent_span_id is None

    return {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name or (str(service_name) if service_name else "span"),
        "kind": "llm" if is_llm else _infer_kind(span),
        "status": _STATUS_BY_CODE.get(span.status_code, "unset"),
        "status_message": span.status_message,
        "start_time": span.start_unix_nano,
        "end_time": span.end_unix_nano,
        "provider": _as_text(first("gen_ai.provider.name", "gen_ai.system")),
        # The response model is the concrete snapshot that was billed.
        "model": _as_text(first("gen_ai.response.model", "gen_ai.request.model")),
        "usage": {
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
        },
        "input": parse_content(first(*_CONTENT_INPUT_KEYS)),
        "output": parse_content(first(*_CONTENT_OUTPUT_KEYS)),
        "attributes": _stored_attributes(attributes, service_name),
        "trace": {
            # Only the root span names the trace; service.name is the fallback.
            "name": (span.name or _as_text(service_name)) if is_root else None,
            "environment": _as_text(
                first("deployment.environment.name", "deployment.environment", source=resource)
            ),
            "release": _as_text(first("service.version", source=resource)),
            "user_id": _as_text(first("user.id") or first("user.id", source=resource)),
            "session_id": _as_text(first("session.id") or first("session.id", source=resource)),
        },
    }


def _infer_kind(span: OtlpSpan) -> str:
    keys = span.attributes.keys()
    if any(key.startswith(("http.", "url.")) for key in keys) and span.kind == _SPAN_KIND_CLIENT:
        return "http"
    if any(key.startswith("db.") for key in keys):
        return "retrieval"
    return "other"


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _stored_attributes(attributes: Mapping[str, Any], service_name: Any) -> dict[str, Any]:
    stored = {key: value for key, value in attributes.items() if key not in _CONTENT_KEYS}
    if service_name is not None:
        stored.setdefault("service.name", service_name)
    return stored
