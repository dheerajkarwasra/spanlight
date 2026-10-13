"""NUL bytes and lone surrogates on ingestion: stripped or replaced, never fatal to the batch."""

import json
import secrets
import time

import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import ingest_native, otlp_json_document, otlp_json_span, stored_spans


async def test_nul_bytes_are_stripped_not_fatal(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await ingest_native(
        client,
        workspace,
        [
            span(
                trace_id=new_trace_id(),
                name="chat\x00 turn",
                status="error",
                status_message="bad\x00 gateway",
                input={"messages": [{"role": "user", "content": "hel\x00lo"}], "ke\x00y": 1},
                output="out\x00put",
                attributes={"att\x00r": "va\x00lue"},
                trace={"name": "tr\x00ace", "tags": ["ta\x00g"]},
            )
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.name == "chat turn"
    assert stored.status_message == "bad gateway"
    assert stored.input == {"messages": [{"role": "user", "content": "hello"}], "key": 1}
    assert stored.output == "output"
    assert stored.attributes["attr"] == "value"
    assert "\x00" not in json.dumps([stored.input, stored.output, stored.attributes])


async def test_a_lone_surrogate_does_not_fail_the_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A `\ud800` escape is valid JSON but cannot be encoded as UTF-8, so it must not reach the
    # database: one span's input would otherwise take the whole batch down with a 500.
    trace_id = new_trace_id()
    batch = [
        span(trace_id=trace_id, input="a\ud800b", name="n\ud800"),
        span(trace_id=trace_id, name="fine"),
    ]
    response = await client.post(
        "/v1/traces",
        content=json.dumps({"spans": batch}),  # ensure_ascii writes the surrogate as an escape
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 2, "rejected": []}
    by_name = {stored.name: stored for stored in await stored_spans(session_factory)}
    assert by_name["n?"].input == "a?b"
    assert "fine" in by_name


async def test_otlp_json_nul_bytes_are_stripped(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    nul_span = otlp_json_span({"k\u0000ey": "v\u0000al"}, name="na\u0000me")
    nul_span["status"] = {"code": 2, "message": "bo\u0000om"}
    document = otlp_json_document(nul_span)
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert stored.name == "name"
    assert stored.status_message == "boom"
    assert stored.attributes["key"] == "val"


async def test_otlp_protobuf_nul_bytes_are_stripped(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Protobuf strings may hold NUL, and no JSON parser strips it on this path.
    now = time.time_ns()
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    scope.spans.append(
        ProtoSpan(
            trace_id=secrets.token_bytes(16),
            span_id=secrets.token_bytes(8),
            name="na\x00me",
            start_time_unix_nano=now - 1000,
            end_time_unix_nano=now,
            status=Status(code=Status.STATUS_CODE_ERROR, message="bo\x00om"),
            attributes=[KeyValue(key="k\x00ey", value=AnyValue(string_value="v\x00al"))],
        )
    )
    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert stored.name == "name"
    assert stored.status_message == "boom"
    assert stored.attributes["key"] == "val"


# A `\\ud800` escape inside the JSON text of a gen_ai message: the OTLP body (or protobuf string)
# is clean, and the surrogate only appears when the message is parsed.
_INNER_SURROGATE = '[{"role": "user", "content": "a\\ud800b"}]'
_CLEANED_MESSAGES = [{"role": "user", "content": "a?b"}]


async def test_otlp_json_lone_surrogate_inside_gen_ai_messages_does_not_fail_the_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = otlp_json_document(
        otlp_json_span({"gen_ai.input.messages": _INNER_SURROGATE}),
        otlp_json_span(name="neighbour"),
    )
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {}
    by_name = {stored.name: stored for stored in await stored_spans(session_factory)}
    assert by_name["ok"].input == _CLEANED_MESSAGES
    assert "neighbour" in by_name


async def test_otlp_protobuf_lone_surrogate_inside_gen_ai_messages_does_not_fail_the_batch(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = time.time_ns()
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    scope.spans.append(
        ProtoSpan(
            trace_id=secrets.token_bytes(16),
            span_id=secrets.token_bytes(8),
            name="ok",
            start_time_unix_nano=now - 1000,
            end_time_unix_nano=now,
            attributes=[
                KeyValue(
                    key="gen_ai.output.messages", value=AnyValue(string_value=_INNER_SURROGATE)
                )
            ],
        )
    )
    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert stored.output == _CLEANED_MESSAGES
