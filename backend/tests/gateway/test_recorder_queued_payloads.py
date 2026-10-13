"""What the span recorder holds while a span waits to be written: bounded and redacted.

A queued span carries the call's input and output as ingestion will store them: secrets
redacted first, then each payload cut to `MAX_PAYLOAD_BYTES`, the span flagged `truncated`. So
a backlog of spans during a database outage holds at most that much per payload, however large
the requests. The request hash is still computed from the whole request.
"""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.core.redact import REDACTED
from app.gateway import call, recorder, span_write
from app.ingest import pipeline
from app.ingest.normalize import MAX_PAYLOAD_BYTES
from app.ingest.request_hash import request_hash
from tests.gateway.support import CHAT_BODY
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    drain_recorder,
    fixture_text,
    logged,
    metric_delta,
    read_gateway_span,
    read_gateway_spans,
)

SECRET = "sk-test-" + "a" * 32  # an OpenAI-shaped key, which redaction replaces
PROMPT_BYTES = 200_000


async def test_a_queued_span_holds_its_payloads_redacted_and_cut(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    content = f"{SECRET} " + "x" * PROMPT_BYTES
    body = {**CHAT_BODY, "messages": [{"role": "user", "content": content}]}
    queued: list[tuple[bool | None, Any]] = []
    real_ingest = pipeline.ingest_spans

    async def spy(db: Any, target: Any, spans: Any, **kwargs: Any) -> Any:
        queued.append((target.prepared_truncated, spans[0]["input"]))
        return await real_ingest(db, target, spans, **kwargs)

    monkeypatch.setattr(span_write, "ingest_spans", spy)

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=body)

    assert response.status_code == 200
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    [(truncated, held)] = queued
    assert truncated is True
    assert isinstance(held, str) and len(held.encode()) <= MAX_PAYLOAD_BYTES
    assert SECRET not in held and REDACTED in held  # redacted before the cut
    assert (span["input"], span["truncated"]) == (held, True)  # stored as held, flagged
    assert span["request_hash"] == request_hash(CHAT_BODY["model"], body)  # the whole request


async def test_a_span_whose_payloads_cannot_be_prepared_is_counted_as_lost(
    gateway_workspace: GatewayWorkspace,
    gateway_client: httpx.AsyncClient,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer = fixture_text("openai/chat.json").encode()
    fake_provider.reply_json(answer)

    def unpreparable(*_: Any, **__: Any) -> Any:
        raise RuntimeError(f"cannot prepare {SECRET}")

    monkeypatch.setattr(recorder, "prepare_span_payloads", unpreparable)
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = (await gateway_client.get("/health/ready")).json()["gateway_spans_lost"]

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)
    await drain_recorder()

    assert (response.status_code, response.content) == (200, answer)  # the client is unaffected
    assert await read_gateway_spans(session_factory, gateway_workspace.project_id) == []
    assert errors.value == 1
    assert (await gateway_client.get("/health/ready")).json()["gateway_spans_lost"] == lost + 1
    [entry] = logged(caplog, "gateway.record_failed")
    assert entry["error_type"] == "RuntimeError"
    assert SECRET not in caplog.text  # neither the message nor a payload reaches the log


async def test_a_span_that_cannot_be_built_is_counted_as_lost(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer = fixture_text("openai/chat.json").encode()
    fake_provider.reply_json(answer)

    def unbuildable(*_: Any, **__: Any) -> Any:
        raise RecursionError(f"cannot build {SECRET}")

    monkeypatch.setattr(call, "build_span", unbuildable)
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    lost = recorder.spans_lost()

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    assert (response.status_code, response.content) == (200, answer)
    assert await read_gateway_spans(session_factory, gateway_workspace.project_id) == []
    assert errors.value == 1
    assert recorder.spans_lost() == lost + 1
    [entry] = logged(caplog, "gateway.span_failed")
    assert (entry["error_type"], entry["stage"]) == ("RecursionError", "build")
    assert SECRET not in caplog.text  # neither the message nor a traceback reaches the log
