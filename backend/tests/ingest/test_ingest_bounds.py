"""Body bounds on ingestion: bodies are cut at the limit before they are buffered.

The native and OTLP routes share one bound (`read_bounded_body`), so the same promises hold on
both: a chunked body or a lying `Content-Length` is refused as soon as the limit is passed, and
the OTLP encodings and content types are accepted alike.
"""

import gzip
import json
import secrets
import time
import zlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.schemas import MAX_BODY_BYTES
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import otlp_json_document, otlp_json_span, stored_spans

MIB = 1024 * 1024


class _CountingBody:
    """An async request body that records how many chunks the server pulled from it."""

    def __init__(self, chunks: int, chunk_size: int = MIB) -> None:
        self.total = chunks
        self.pulled = 0
        self._chunk = b" " * chunk_size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.total):
            self.pulled += 1
            yield self._chunk


def _proto_span(name: str = "ok", *, attributes: list[KeyValue] | None = None) -> ProtoSpan:
    now = time.time_ns()
    return ProtoSpan(
        trace_id=secrets.token_bytes(16),
        span_id=secrets.token_bytes(8),
        name=name,
        start_time_unix_nano=now - 1_000_000,
        end_time_unix_nano=now,
        attributes=attributes or [],
    )


def _proto_request(spans: list[ProtoSpan], *, scopes: int = 1) -> bytes:
    """The spans spread over `scopes` scopeSpans, serialized."""
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    for index in range(scopes):
        resource_spans.scope_spans.add().spans.extend(spans[index::scopes])
    return bytes(request.SerializeToString())


async def test_chunked_body_over_limit_is_413_without_buffering(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Ten 1 MiB chunks and no Content-Length: the server must stop pulling past the 5 MiB limit.
    body = _CountingBody(chunks=10)
    response = await client.post(
        "/v1/traces",
        content=body,
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert body.pulled <= MAX_BODY_BYTES // MIB + 1
    assert body.pulled < body.total
    assert await stored_spans(session_factory) == []


async def test_lying_content_length_is_cut_at_the_limit(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = _CountingBody(chunks=10)
    response = await client.post(
        "/v1/traces",
        content=body,
        headers={
            **bearer(workspace.api_key),
            "Content-Type": "application/json",
            "Content-Length": "10",  # the body is 10 MiB
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert body.pulled <= MAX_BODY_BYTES // MIB + 1
    assert await stored_spans(session_factory) == []


async def test_otlp_chunked_body_over_limit_is_413(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    body = _CountingBody(chunks=10)
    response = await client.post(
        "/v1/otlp/traces",
        content=body,
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 413
    assert body.pulled <= MAX_BODY_BYTES // MIB + 1


async def test_otlp_protobuf_over_5_mib_is_413(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    big = KeyValue(key="blob", value=AnyValue(string_value="x" * (MAX_BODY_BYTES + 1)))
    payload = _proto_request([_proto_span(attributes=[big])])
    assert len(payload) > MAX_BODY_BYTES

    response = await client.post(
        "/v1/otlp/traces",
        content=payload,
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert await stored_spans(session_factory) == []


async def test_otlp_gzip_protobuf_is_accepted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = _proto_request([_proto_span()])
    response = await client.post(
        "/v1/otlp/traces",
        content=gzip.compress(payload),
        headers={
            **bearer(workspace.api_key),
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "gzip",
        },
    )

    assert response.status_code == 200
    assert len(await stored_spans(session_factory)) == 1


async def test_otlp_deflate_json_is_accepted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = otlp_json_document(otlp_json_span())
    response = await client.post(
        "/v1/otlp/traces",
        content=zlib.compress(json.dumps(document).encode()),
        headers={
            **bearer(workspace.api_key),
            "Content-Type": "application/json",
            "Content-Encoding": "deflate",
        },
    )

    assert response.status_code == 200
    assert len(await stored_spans(session_factory)) == 1


@pytest.mark.parametrize("content_type", ["application/json; charset=utf-8", "Application/JSON"])
async def test_otlp_content_type_parameters_and_case_are_ignored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    content_type: str,
) -> None:
    document = otlp_json_document(otlp_json_span())
    response = await client.post(
        "/v1/otlp/traces",
        content=json.dumps(document),
        headers={**bearer(workspace.api_key), "Content-Type": content_type},
    )

    assert response.status_code == 200
    assert len(await stored_spans(session_factory)) == 1


async def test_otlp_protobuf_span_count_over_limit_is_413(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = _proto_request([_proto_span() for _ in range(1001)], scopes=3)
    response = await client.post(
        "/v1/otlp/traces",
        content=payload,
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert await stored_spans(session_factory) == []


async def test_otlp_json_span_count_over_limit_is_413_and_the_limit_itself_is_accepted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def document(count: int) -> dict[str, Any]:
        spans = [otlp_json_span() for _ in range(count)]
        return {
            "resourceSpans": [
                {"scopeSpans": [{"spans": spans[:400]}, {"spans": spans[400:]}]},
            ]
        }

    headers = bearer(workspace.api_key)
    over = await client.post("/v1/otlp/traces", json=document(1001), headers=headers)
    assert over.status_code == 413
    assert await stored_spans(session_factory) == []

    exact = await client.post("/v1/otlp/traces", json=document(1000), headers=headers)
    assert exact.status_code == 200
    assert len(await stored_spans(session_factory)) == 1000


async def test_native_batch_of_exactly_the_span_limit_is_accepted(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    # The control for the 413 above: the native limit is on the same count.
    spans = [span(trace_id=new_trace_id()) for _ in range(1000)]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1000
