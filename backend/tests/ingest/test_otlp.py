"""OTLP/HTTP ingestion in both encodings, mapped onto native spans."""

import json
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
from opentelemetry.proto.trace.v1.trace_pb2 import Status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from tests.helpers import Workspace, bearer

RESOURCE = {
    "service.name": "checkout-api",
    "deployment.environment.name": "staging",
    "service.version": "2.3.0",
}
LLM_ATTRIBUTES: dict[str, Any] = {
    "gen_ai.system": "openai",
    "gen_ai.request.model": "gpt-4o",
    "gen_ai.response.model": "gpt-4o-2024-08-06",
    "gen_ai.usage.input_tokens": 2000,
    "gen_ai.usage.output_tokens": 100,
    "gen_ai.usage.cache_read_input_tokens": 1000,
    "gen_ai.input.messages": json.dumps([{"role": "user", "content": "hello"}]),
    "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": "hi!"}]),
    "session.id": "sess-42",
    "user.id": "user-7",
}


def _ids() -> tuple[bytes, bytes, bytes]:
    return secrets.token_bytes(16), secrets.token_bytes(8), secrets.token_bytes(8)


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}  # proto3 JSON encodes int64 as a string
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def _json_attributes(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _json_value(value)} for key, value in attributes.items()]


def _proto_attributes(attributes: dict[str, Any]) -> list[KeyValue]:
    def any_value(value: Any) -> AnyValue:
        if isinstance(value, bool):
            return AnyValue(bool_value=value)
        if isinstance(value, int):
            return AnyValue(int_value=value)
        return AnyValue(string_value=value)

    return [KeyValue(key=key, value=any_value(value)) for key, value in attributes.items()]


async def _stored(session_factory: async_sessionmaker[AsyncSession]) -> tuple[list[Span], Trace]:
    async with session_factory() as db:
        await bypass_rls(db)
        spans = list((await db.scalars(select(Span).order_by(Span.started_at))).all())
        trace = (await db.scalars(select(Trace))).one()
    return spans, trace


def _assert_mapped(spans: list[Span], trace: Trace) -> None:
    root, llm = spans
    assert root.kind.value == "other" and root.parent_span_id is None
    assert llm.kind.value == "llm"
    assert llm.provider == "openai"
    assert llm.model == "gpt-4o-2024-08-06"
    assert (llm.input_tokens, llm.output_tokens, llm.cached_tokens) == (2000, 100, 1000)
    # 1000 uncached * 2.50 + 1000 cached * 1.25 + 100 output * 10.00, per 1M tokens
    assert str(llm.cost_usd) == "0.00475000"
    assert llm.input == [{"role": "user", "content": "hello"}]
    assert llm.output == [{"role": "assistant", "content": "hi!"}]
    assert "gen_ai.input.messages" not in llm.attributes
    assert llm.attributes["service.name"] == "checkout-api"
    assert llm.status.value == "error" and llm.status_message == "rate limited"
    assert trace.name == "POST /checkout"
    assert trace.environment == "staging"
    assert trace.release == "2.3.0"
    assert trace.session_id == "sess-42"
    assert trace.external_user_id == "user-7"
    assert trace.span_count == 2 and trace.error_count == 1


async def test_otlp_json(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, root_id, child_id = _ids()
    now = time.time_ns()
    document = {
        "resourceSpans": [
            {
                "resource": {"attributes": _json_attributes(RESOURCE)},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id.hex(),
                                "spanId": root_id.hex(),
                                "name": "POST /checkout",
                                "kind": 2,
                                "startTimeUnixNano": str(now - 2_000_000_000),
                                "endTimeUnixNano": str(now),
                                "status": {"code": 1},
                            },
                            {
                                "traceId": trace_id.hex(),
                                "spanId": child_id.hex(),
                                "parentSpanId": root_id.hex(),
                                "name": "chat gpt-4o",
                                "kind": "SPAN_KIND_CLIENT",
                                "startTimeUnixNano": str(now - 1_500_000_000),
                                "endTimeUnixNano": str(now - 500_000_000),
                                "status": {"code": 2, "message": "rate limited"},
                                "attributes": _json_attributes(LLM_ATTRIBUTES),
                            },
                        ]
                    }
                ],
            }
        ]
    }
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200
    assert response.json() == {}
    _assert_mapped(*await _stored(session_factory))


async def test_otlp_protobuf(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id, root_id, child_id = _ids()
    now = time.time_ns()
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    resource_spans.resource.attributes.extend(_proto_attributes(RESOURCE))
    scope = resource_spans.scope_spans.add()
    scope.spans.extend(
        [
            ProtoSpan(
                trace_id=trace_id,
                span_id=root_id,
                name="POST /checkout",
                kind=ProtoSpan.SPAN_KIND_SERVER,
                start_time_unix_nano=now - 2_000_000_000,
                end_time_unix_nano=now,
                status=Status(code=Status.STATUS_CODE_OK),
            ),
            ProtoSpan(
                trace_id=trace_id,
                span_id=child_id,
                parent_span_id=root_id,
                name="chat gpt-4o",
                kind=ProtoSpan.SPAN_KIND_CLIENT,
                start_time_unix_nano=now - 1_500_000_000,
                end_time_unix_nano=now - 500_000_000,
                status=Status(code=Status.STATUS_CODE_ERROR, message="rate limited"),
                attributes=_proto_attributes(LLM_ATTRIBUTES),
            ),
        ]
    )
    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-protobuf"
    decoded = ExportTraceServiceResponse.FromString(response.content)
    assert decoded.partial_success.rejected_spans == 0
    _assert_mapped(*await _stored(session_factory))


async def test_otlp_partial_success_reports_rejections(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    now = time.time_ns()
    document = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": secrets.token_hex(16),
                                "spanId": secrets.token_hex(8),
                                "name": "ok",
                                "startTimeUnixNano": str(now - 1000),
                                "endTimeUnixNano": str(now),
                            },
                            {
                                "traceId": "00" * 16,
                                "spanId": secrets.token_hex(8),
                                "name": "zero trace id",
                                "startTimeUnixNano": str(now - 1000),
                                "endTimeUnixNano": str(now),
                            },
                        ]
                    }
                ]
            }
        ]
    }
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )
    partial = response.json()["partialSuccess"]
    assert partial["rejectedSpans"] == "1"
    assert "trace_id" in partial["errorMessage"]


async def test_otlp_rejects_bad_bodies(client: httpx.AsyncClient, workspace: Workspace) -> None:
    headers = bearer(workspace.api_key)
    garbage = await client.post(
        "/v1/otlp/traces",
        content=b"\xff\xff\xff",
        headers={**headers, "Content-Type": "application/x-protobuf"},
    )
    assert garbage.status_code == 400
    unsupported = await client.post(
        "/v1/otlp/traces", content=b"x", headers={**headers, "Content-Type": "text/plain"}
    )
    assert unsupported.status_code == 415
