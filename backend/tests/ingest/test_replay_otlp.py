"""OTLP re-exports are idempotent in both encodings, and native and OTLP spans share traces."""

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import Workspace, bearer, span
from tests.ingest.support import ingest_native, otlp_json_document, telemetry_rows

ENVIRONMENT = {"deployment.environment.name": "staging", "service.version": "2.3.0"}
LLM_ATTRIBUTES = {
    "gen_ai.system": "openai",
    "gen_ai.request.model": "gpt-4o-mini",
    "gen_ai.usage.input_tokens": 700,
    "gen_ai.usage.output_tokens": 70,
}


class ExportedTrace:
    """One trace, a root and an LLM child, that can be sent as OTLP JSON or as protobuf."""

    def __init__(self) -> None:
        self.trace_id = secrets.token_bytes(16)
        self.root_id = secrets.token_bytes(8)
        self.child_id = secrets.token_bytes(8)
        self.end_ns = time.time_ns() - 60_000_000_000

    def json(self) -> dict[str, Any]:
        def string(value: str) -> dict[str, Any]:
            return {"stringValue": value}

        def attributes(items: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "key": key,
                    "value": {"intValue": str(value)} if isinstance(value, int) else string(value),
                }
                for key, value in items.items()
            ]

        root = {
            "traceId": self.trace_id.hex(),
            "spanId": self.root_id.hex(),
            "name": "answer",
            "startTimeUnixNano": str(self.end_ns - 2_000_000_000),
            "endTimeUnixNano": str(self.end_ns),
        }
        child = {
            "traceId": self.trace_id.hex(),
            "spanId": self.child_id.hex(),
            "parentSpanId": self.root_id.hex(),
            "name": "chat gpt-4o-mini",
            "startTimeUnixNano": str(self.end_ns - 1_500_000_000),
            "endTimeUnixNano": str(self.end_ns - 500_000_000),
            "attributes": attributes(LLM_ATTRIBUTES),
        }
        document = otlp_json_document(root, child)
        document["resourceSpans"][0]["resource"] = {"attributes": attributes(ENVIRONMENT)}
        return document

    def protobuf(self) -> bytes:
        def attributes(items: dict[str, Any]) -> list[KeyValue]:
            return [
                KeyValue(
                    key=key,
                    value=AnyValue(int_value=value)
                    if isinstance(value, int)
                    else AnyValue(string_value=value),
                )
                for key, value in items.items()
            ]

        request = ExportTraceServiceRequest()
        resource_spans = request.resource_spans.add()
        resource_spans.resource.attributes.extend(attributes(ENVIRONMENT))
        resource_spans.scope_spans.add().spans.extend(
            [
                ProtoSpan(
                    trace_id=self.trace_id,
                    span_id=self.root_id,
                    name="answer",
                    start_time_unix_nano=self.end_ns - 2_000_000_000,
                    end_time_unix_nano=self.end_ns,
                ),
                ProtoSpan(
                    trace_id=self.trace_id,
                    span_id=self.child_id,
                    parent_span_id=self.root_id,
                    name="chat gpt-4o-mini",
                    start_time_unix_nano=self.end_ns - 1_500_000_000,
                    end_time_unix_nano=self.end_ns - 500_000_000,
                    attributes=attributes(LLM_ATTRIBUTES),
                ),
            ]
        )
        serialized: bytes = request.SerializeToString()
        return serialized


async def _export_json(
    client: httpx.AsyncClient, workspace: Workspace, exported: ExportedTrace
) -> None:
    response = await client.post(
        "/v1/otlp/traces", json=exported.json(), headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200, response.text


async def _export_protobuf(
    client: httpx.AsyncClient, workspace: Workspace, exported: ExportedTrace
) -> None:
    response = await client.post(
        "/v1/otlp/traces",
        content=exported.protobuf(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )
    assert response.status_code == 200, response.text


async def test_otlp_reexport_is_idempotent_in_both_encodings(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exported = ExportedTrace()

    await _export_json(client, workspace, exported)
    first = await telemetry_rows(session_factory, workspace.project_id)
    await _export_protobuf(client, workspace, exported)
    second = await telemetry_rows(session_factory, workspace.project_id)
    await _export_json(client, workspace, exported)
    third = await telemetry_rows(session_factory, workspace.project_id)

    spans, [trace] = first
    assert len(spans) == 2
    assert (trace["span_count"], trace["input_tokens"], trace["output_tokens"]) == (2, 700, 70)
    assert trace["cost_usd"] is not None
    assert first == second == third  # `telemetry_rows` leaves out `ingested_at`


async def test_native_and_otlp_for_the_same_ids_converge(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exported = ExportedTrace()
    start = datetime.now(UTC) - timedelta(minutes=5)
    native_root = span(
        trace_id=exported.trace_id.hex(),
        span_id=exported.root_id.hex(),
        kind="chain",
        model=None,
        usage=None,
        start=start,
        trace={"name": "from native", "tags": ["native"], "environment": "prod"},
    )
    assert (await ingest_native(client, workspace, [native_root])).status_code == 200

    await _export_json(client, workspace, exported)

    spans, traces = await telemetry_rows(session_factory, workspace.project_id)
    [trace] = traces  # one trace, whichever endpoint wrote which span
    assert {row["span_id"] for row in spans} == {exported.root_id.hex(), exported.child_id.hex()}
    assert trace["span_count"] == 2
    # The OTLP export replaced the native root (same ids) and set the fields it knows; the
    # tag only the native batch had stays.
    root = next(row for row in spans if row["span_id"] == exported.root_id.hex())
    assert root["name"] == "answer"
    assert trace["environment"] == "staging"
    assert trace["release"] == "2.3.0"
    assert trace["tags"] == ["native"]
    assert (trace["input_tokens"], trace["output_tokens"]) == (700, 70)
