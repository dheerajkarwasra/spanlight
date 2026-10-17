"""OTLP/HTTP trace export: decoding both encodings into `OtlpSpan`, and the responses.

The mapping onto the native `SpanIn` shape is `otlp_span` (through the convention registry);
its entry points are re-exported here for the router and the tests.
"""

import base64
from collections.abc import Iterable
from typing import Any

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

from app.core.text import storable_double
from app.ingest.otlp_span import OtlpSpan, count_conventions, to_native_span, to_native_spans
from app.ingest.otlp_values import (
    OtlpDecodeError,
    json_attributes,
    json_id,
    json_int,
    member,
    member_dict,
    member_list,
)

__all__ = [
    "OtlpSpan",
    "count_conventions",
    "decode_json",
    "decode_protobuf",
    "encode_protobuf_response",
    "json_response",
    "to_native_span",
    "to_native_spans",
]


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
    if rejected or message:
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
    # OTLP allows `rejectedSpans: 0` with a message: a warning that every span was accepted.
    if not rejected and not message:
        return {}
    return {"partialSuccess": {"rejectedSpans": str(rejected), "errorMessage": message or ""}}
