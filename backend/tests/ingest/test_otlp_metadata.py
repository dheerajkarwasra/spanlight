"""OTLP `spanlight.metadata.<key>` attributes become trace metadata (spec §4.2, §4.5, §4.6).

The attributes are read on any span and on the resource, the span winning per key; the 32-key
cap applies after that merge, in code-point key order. They are trace-level, so they are removed
from the span's stored `attributes`. The OTLP path is lenient: an invalid entry is dropped,
counted in `spanlight_trace_metadata_dropped_total{path="otlp"}` and in `DroppedValues.metadata`,
and named (by count, never by value) in the `partialSuccess.errorMessage`.
"""

import secrets
import time
from typing import Any

import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from prometheus_client import REGISTRY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Trace
from app.db.rls import bypass_rls
from app.ingest.metadata import MetadataDrops, metadata_from_attributes
from app.ingest.otlp import OtlpSpan, to_native_spans
from tests.helpers import Workspace, bearer
from tests.ingest.support import otlp_json_span, stored_spans

PREFIX = "spanlight.metadata."
REASONS = ("key", "value", "type", "count")


def _span(
    attributes: dict[str, Any],
    resource: dict[str, Any] | None = None,
    *,
    parent_span_id: str | None = None,
) -> OtlpSpan:
    now = time.time_ns()
    return OtlpSpan(
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        parent_span_id=parent_span_id,
        name="step",
        kind=0,
        start_unix_nano=now - 1_000_000,
        end_unix_nano=now,
        status_code=0,
        status_message=None,
        attributes=attributes,
        resource=resource or {},
    )


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_json_value(item) for item in value]}}
    return {"stringValue": value}


def _json_attributes(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _json_value(value)} for key, value in attributes.items()]


def _json_document(resource: dict[str, Any], *spans: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _json_attributes(resource)},
                "scopeSpans": [{"spans": list(spans)}],
            }
        ]
    }


def _json_span(
    attributes: dict[str, Any], *, trace_id: str | None = None, parent: str | None = None
) -> dict[str, Any]:
    span = otlp_json_span()
    span["attributes"] = _json_attributes(attributes)
    if trace_id is not None:
        span["traceId"] = trace_id
    if parent is not None:
        span["parentSpanId"] = parent
    return span


async def _post(
    client: httpx.AsyncClient, workspace: Workspace, document: dict[str, Any]
) -> httpx.Response:
    return await client.post("/v1/otlp/traces", json=document, headers=bearer(workspace.api_key))


async def _stored_trace(session_factory: async_sessionmaker[AsyncSession]) -> Trace:
    async with session_factory() as db:
        await bypass_rls(db)
        (trace,) = (await db.scalars(select(Trace))).all()
        return trace


def _dropped(reason: str) -> float:
    value = REGISTRY.get_sample_value(
        "spanlight_trace_metadata_dropped_total", {"path": "otlp", "reason": reason}
    )
    return value or 0.0


# --- mapping ------------------------------------------------------------------


def test_span_and_resource_metadata_merge_with_span_winning() -> None:
    resource = {"service.name": "search", f"{PREFIX}team": "search", f"{PREFIX}tier": "free"}
    attributes = {f"{PREFIX}tier": "pro", f"{PREFIX}customer_id": 42, f"{PREFIX}beta": True}

    [native], dropped = to_native_spans([_span(attributes, resource)])

    assert native["trace"]["metadata"] == {
        "beta": "true",
        "customer_id": "42",
        "team": "search",
        "tier": "pro",
    }
    assert dropped.metadata == 0 and dropped.total == 0


def test_metadata_on_a_non_root_span_reaches_the_trace() -> None:
    child = _span({f"{PREFIX}feature": "summarize"}, parent_span_id=secrets.token_hex(8))

    [native], _ = to_native_spans([child])

    assert native["trace"]["metadata"] == {"feature": "summarize"}
    assert native["trace"]["name"] is None  # still only the root names the trace


def test_a_span_without_metadata_maps_an_empty_object() -> None:
    [native], _ = to_native_spans([_span({"http.method": "GET"})])

    assert native["trace"]["metadata"] == {}


def test_metadata_attributes_are_removed_from_the_mapped_attributes() -> None:
    attributes = {f"{PREFIX}team": "search", "http.method": "GET", "spanlight.other": "kept"}

    [native], _ = to_native_spans([_span(attributes, {f"{PREFIX}tier": "pro"})])

    assert native["attributes"] == {
        "http.method": "GET",
        "spanlight.other": "kept",
        "spanlight.convention": "none",
    }


def test_the_32_key_cap_applies_after_the_merge_in_code_point_order() -> None:
    resource = {f"{PREFIX}a{index:02}": "r" for index in range(20)}
    attributes = {f"{PREFIX}b{index:02}": "s" for index in range(20)}
    attributes[f"{PREFIX}a00"] = "span"  # overrides a resource key: not a drop
    before = _dropped("count")

    [native], dropped = to_native_spans([_span(attributes, resource)])

    metadata = native["trace"]["metadata"]
    assert sorted(metadata) == [f"a{i:02}" for i in range(20)] + [f"b{i:02}" for i in range(12)]
    assert metadata["a00"] == "span"
    assert dropped.metadata == 8
    assert _dropped("count") == before + 8


def test_invalid_entries_are_dropped_and_counted_by_reason() -> None:
    attributes = {
        f"{PREFIX}ok": "kept",
        f"{PREFIX}bad key": "x",  # space: still fails the key pattern after lower-casing
        f"{PREFIX}long": "v" * 257,
        f"{PREFIX}list": ["a", "b"],
        f"{PREFIX}CamelCase": "folded",  # an exporter's spelling: lower-cased, kept
    }
    before = {reason: _dropped(reason) for reason in REASONS}

    [native], dropped = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"ok": "kept", "camelcase": "folded"}
    assert dropped.metadata == 3 and dropped.total == 3
    after = {reason: _dropped(reason) - before[reason] for reason in REASONS}
    assert after == {"key": 1, "value": 1, "type": 1, "count": 0}


def test_metadata_values_are_cleaned_of_nul_before_they_are_validated() -> None:
    # A protobuf string may hold NUL, which Postgres cannot store: removed first, so a value of
    # only NUL is an empty value (dropped), not an empty string that strict validation rejects.
    attributes = {f"{PREFIX}joined": "a\x00b", f"{PREFIX}empty": "\x00"}

    [native], dropped = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"joined": "ab"}
    assert dropped.metadata == 1


def test_foreign_prefix_through_metadata_from_attributes() -> None:
    drops = MetadataDrops()
    attributes = {"ai.telemetry.metadata.userId": "u-1", "ai.telemetry.functionId": "chat"}

    found = metadata_from_attributes(attributes, prefix="ai.telemetry.metadata.", drops=drops)

    assert found == {"userid": "u-1"}
    assert drops.total == 0


# --- convention prefixes (Bet 4, spec §7) -------------------------------------


def test_vercel_telemetry_metadata_reaches_the_trace() -> None:
    attributes = {"ai.operationId": "ai.generateText", "ai.telemetry.metadata.tenant": "acme"}

    [native], dropped = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"tenant": "acme"}
    assert "ai.telemetry.metadata.tenant" not in native["attributes"]
    assert dropped.total == 0


def test_vercel_seven_settings_context_prefix() -> None:
    attributes = {"ai.operationId": "ai.generateText", "ai.settings.context.tenant": "acme"}

    [native], _ = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"tenant": "acme"}


def test_openinference_metadata_object_values_coerced() -> None:
    attributes = {
        "openinference.span.kind": "LLM",
        "metadata": '{"count": 3, "beta": true, "team": "search", "tags": ["a"]}',
    }
    before = _dropped("type")

    [native], dropped = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"beta": "true", "count": "3", "team": "search"}
    assert dropped.metadata == 1 and _dropped("type") == before + 1  # the array


def test_traceloop_association_properties() -> None:
    attributes = {
        "traceloop.span.kind": "workflow",
        "traceloop.association.properties.user_id": "u-1",
    }

    [native], _ = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"user_id": "u-1"}


def test_spanlight_metadata_wins_over_convention_metadata() -> None:
    on_span = {
        "ai.operationId": "ai.generateText",
        "ai.telemetry.metadata.tenant": "acme",
        "ai.telemetry.metadata.team": "search",
        f"{PREFIX}tenant": "globex",
    }
    on_resource = {"ai.operationId": "ai.generateText", "ai.telemetry.metadata.tenant": "acme"}

    [span_wins, resource_wins], _ = to_native_spans(
        [_span(on_span), _span(on_resource, {f"{PREFIX}tenant": "initech"})]
    )

    assert span_wins["trace"]["metadata"] == {"tenant": "globex", "team": "search"}
    assert resource_wins["trace"]["metadata"] == {"tenant": "initech"}


def test_key_with_a_space_is_dropped_and_counted() -> None:
    attributes = {"ai.operationId": "ai.generateText", "ai.telemetry.metadata.tenant id": "acme"}
    before = _dropped("key")

    [native], dropped = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {}
    assert dropped.metadata == 1 and dropped.total == 1
    assert _dropped("key") == before + 1


def test_tenant_dash_and_underscore_stay_distinct() -> None:
    attributes = {
        "ai.operationId": "ai.generateText",
        "ai.telemetry.metadata.tenant-id": "a",
        "ai.telemetry.metadata.tenant_id": "b",
    }

    [native], _ = to_native_spans([_span(attributes)])

    assert native["trace"]["metadata"] == {"tenant-id": "a", "tenant_id": "b"}


# --- through the endpoint -----------------------------------------------------


async def test_metadata_attributes_are_not_stored_in_span_attributes(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = _json_span({f"{PREFIX}team": "search", "http.method": "GET"})
    response = await _post(client, workspace, _json_document({f"{PREFIX}tier": "pro"}, span))

    assert response.status_code == 200 and response.json() == {}
    [stored] = await stored_spans(session_factory)
    assert stored.attributes["http.method"] == "GET"
    assert not [key for key in stored.attributes if key.startswith(PREFIX)]


async def test_invalid_metadata_is_dropped_counted_and_named_in_partial_success(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    secret_value = "v" * 257
    span = _json_span(
        {
            f"{PREFIX}ok": "kept",
            f"{PREFIX}bad key": "x",
            f"{PREFIX}long": secret_value,
            f"{PREFIX}list": ["a", "b"],
        }
    )
    before = {reason: _dropped(reason) for reason in REASONS}

    response = await _post(client, workspace, _json_document({}, span))

    assert response.status_code == 200
    partial = response.json()["partialSuccess"]
    assert partial["rejectedSpans"] == "0"
    assert partial["errorMessage"].startswith("trace metadata: 3 entries dropped")
    assert secret_value not in partial["errorMessage"] and "bad key" not in partial["errorMessage"]
    after = {reason: _dropped(reason) - before[reason] for reason in REASONS}
    assert after == {"key": 1, "value": 1, "type": 1, "count": 0}
    assert len(await stored_spans(session_factory)) == 1  # the span is kept


async def test_metadata_drops_are_named_in_the_protobuf_partial_success(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    now = time.time_ns()
    scope.spans.append(
        ProtoSpan(
            trace_id=secrets.token_bytes(16),
            span_id=secrets.token_bytes(8),
            name="step",
            start_time_unix_nano=now - 1_000_000,
            end_time_unix_nano=now,
            attributes=[
                KeyValue(key=f"{PREFIX}Bad Key", value=AnyValue(string_value="x")),
            ],
        )
    )
    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 200
    decoded = ExportTraceServiceResponse()
    decoded.ParseFromString(response.content)
    assert decoded.partial_success.rejected_spans == 0
    assert decoded.partial_success.error_message.startswith("trace metadata: 1 entry dropped")


async def test_span_and_resource_metadata_are_stored_on_the_trace(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    resource = {f"{PREFIX}team": "search", f"{PREFIX}tier": "free"}
    span = _json_span({f"{PREFIX}tier": "pro", f"{PREFIX}customer_id": 42})

    response = await _post(client, workspace, _json_document(resource, span))

    assert response.status_code == 200 and response.json() == {}
    trace = await _stored_trace(session_factory)
    assert trace.metadata_ == {"team": "search", "tier": "pro", "customer_id": "42"}


async def test_metadata_on_a_non_root_span_is_stored_on_the_trace(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    root = _json_span({"http.method": "GET"})
    child = _json_span(
        {f"{PREFIX}feature": "summarize"}, trace_id=root["traceId"], parent=root["spanId"]
    )

    response = await _post(client, workspace, _json_document({}, root, child))

    assert response.status_code == 200 and response.json() == {}
    trace = await _stored_trace(session_factory)
    assert trace.metadata_ == {"feature": "summarize"}
