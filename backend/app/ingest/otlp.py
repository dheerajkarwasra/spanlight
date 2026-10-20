"""OTLP/HTTP trace export: decoding both encodings into `OtlpSpan`, and the responses.

The mapping onto the native `SpanIn` shape is `otlp_span` (through the convention registry);
its entry points are re-exported here for the router and the tests.
"""

import base64
from collections.abc import Iterable
from functools import cache
from typing import Any

from google.protobuf.descriptor import Descriptor
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

from app.core.text import storable_double
from app.ingest.otlp_span import OtlpSpan, count_conventions, to_native_span, to_native_spans
from app.ingest.otlp_values import (
    OtlpDecodeError,
    TooManySpansError,
    json_attributes,
    json_id,
    json_int,
    member,
    member_dict,
    member_list,
)

__all__ = [
    "OtlpSpan",
    "TooManySpansError",
    "count_conventions",
    "decode_json",
    "decode_protobuf",
    "encode_protobuf_response",
    "json_response",
    "json_span_count",
    "to_native_span",
    "to_native_spans",
]


# --- protobuf -----------------------------------------------------------------


def decode_protobuf(body: bytes, *, max_spans: int) -> list[OtlpSpan]:
    """Decode the request; more than `max_spans` spans is `TooManySpansError`, raised before
    the body is parsed (millions of empty spans fit in a few kilobytes of gzip)."""
    _check_wire(body, max_spans=max_spans)
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


# upb allocates every message of a body as it parses (a raw 5 MiB of empty spans took ~470 MB),
# so the body's messages are counted from the wire format first. 1000 spans x 128 attributes
# (the OTel SDKs' default attribute count limit) x 2 messages (KeyValue, AnyValue) is 256,000;
# the rest is headroom for resources, events and links.
MAX_PROTOBUF_MESSAGES = 300_000
_MAX_DEPTH = 100  # upb's own recursion limit
_PER_SPAN = (ResourceSpans.DESCRIPTOR, ScopeSpans.DESCRIPTOR, Span.DESCRIPTOR)


def _check_wire(body: bytes, *, max_spans: int) -> None:
    """Refuse the body before it is parsed: more than `max_spans` ResourceSpans, ScopeSpans or
    Spans is `TooManySpansError`; more than `MAX_PROTOBUF_MESSAGES` messages in all, nesting past
    `_MAX_DEPTH` or wire data that cannot be stepped over is `OtlpDecodeError`. The walk stops at
    the first bound passed, so its own cost is bounded too."""
    counts = dict.fromkeys(_PER_SPAN, 0)
    total = 0

    def walk(pos: int, end: int, descriptor: Descriptor, depth: int) -> None:
        nonlocal total
        children = _message_fields(descriptor)
        while pos < end:
            key = body[pos]  # one-byte varints inline: this loop visits every field of the body
            if key < 0x80:
                pos += 1
            else:
                key, pos = _varint(body, pos, end)
            wire = key & 7
            if wire == 2:
                if pos < end and body[pos] < 0x80:
                    length = body[pos]
                    pos += 1
                else:
                    length, pos = _varint(body, pos, end)
                child = children.get(key >> 3)
                if child is not None and pos + length <= end:
                    total += 1
                    if total > MAX_PROTOBUF_MESSAGES or depth == _MAX_DEPTH:
                        raise OtlpDecodeError(
                            f"body holds more than {MAX_PROTOBUF_MESSAGES} protobuf messages"
                            f" or nests them more than {_MAX_DEPTH} deep"
                        )
                    if child in counts:
                        counts[child] += 1
                        if counts[child] > max_spans:
                            raise TooManySpansError
                    walk(pos, pos + length, child, depth + 1)
                pos += length
            else:
                pos = _skip_scalar(body, pos, end, wire)
            if pos > end or key < 8:
                raise OtlpDecodeError("body is not a valid ExportTraceServiceRequest")

    walk(0, len(body), ExportTraceServiceRequest.DESCRIPTOR, 0)


@cache
def _message_fields(descriptor: Descriptor) -> dict[int, Descriptor]:
    return {f.number: f.message_type for f in descriptor.fields if f.message_type is not None}


def _skip_scalar(body: bytes, pos: int, end: int, wire: int) -> int:
    if wire == 0:
        return _varint(body, pos, end)[1]
    if wire == 1:
        return pos + 8
    if wire == 5:
        return pos + 4
    raise OtlpDecodeError("body is not a valid ExportTraceServiceRequest")  # groups, bad types


def _varint(body: bytes, pos: int, end: int) -> tuple[int, int]:
    result = shift = 0
    while pos < end and shift < 64:
        byte = body[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
    raise OtlpDecodeError("body is not a valid ExportTraceServiceRequest")


def encode_protobuf_response(rejected: int, message: str | None) -> bytes:
    response = ExportTraceServiceResponse()
    if rejected or message:
        response.partial_success.CopyFrom(
            ExportTracePartialSuccess(rejected_spans=rejected, error_message=message or "")
        )
    serialized: bytes = response.SerializeToString()
    return serialized


# --- JSON ---------------------------------------------------------------------


def json_span_count(document: Any) -> int:
    """How many spans an OTLP/JSON document holds, counted without decoding any of them.

    Misshapen parts count as no spans and never raise: `decode_json` refuses them later.
    """
    if not isinstance(document, dict):
        return 0
    return sum(
        len(member_list(scope_spans, "spans"))
        for resource_spans in member_list(document, "resourceSpans", "resource_spans")
        if isinstance(resource_spans, dict)
        for scope_spans in member_list(resource_spans, "scopeSpans", "scope_spans")
        if isinstance(scope_spans, dict)
    )


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
        kind=json_int(member(span, "kind"), enum=True),
        start_unix_nano=json_int(member(span, "startTimeUnixNano", "start_time_unix_nano")),
        end_unix_nano=json_int(member(span, "endTimeUnixNano", "end_time_unix_nano")),
        status_code=json_int(member(status, "code"), enum=True),
        status_message=member(status, "message") or None,
        attributes=json_attributes(member_list(span, "attributes")),
        resource=resource,
    )


def json_response(rejected: int, message: str | None) -> dict[str, Any]:
    # OTLP allows `rejectedSpans: 0` with a message: a warning that every span was accepted.
    if not rejected and not message:
        return {}
    return {"partialSuccess": {"rejectedSpans": str(rejected), "errorMessage": message or ""}}
