"""Kind tables: every row of spec §4.4 per convention, plus the no-convention inference."""

from typing import Any

import pytest

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.kinds import (
    infer_kind,
    is_embeddings_operation,
    openinference_kind,
    operation_kind,
    traceloop_kind,
    vercel_kind,
)
from app.ingest.conventions.types import Convention


@pytest.mark.parametrize(
    ("operation", "kind"),
    [
        ("chat", SpanKind.LLM),
        ("text_completion", SpanKind.LLM),
        ("generate_content", SpanKind.LLM),
        ("embeddings", SpanKind.LLM),
        ("execute_tool", SpanKind.TOOL),
        ("invoke_agent", SpanKind.CHAIN),
        ("create_agent", SpanKind.CHAIN),
        ("invoke_workflow", SpanKind.CHAIN),
        ("retrieval", SpanKind.RETRIEVAL),
        ("fetch_response", SpanKind.OTHER),
        ("create_memory", SpanKind.OTHER),
        ("zzz", SpanKind.OTHER),
        (None, SpanKind.LLM),
    ],
)
def test_operation_kind(operation: str | None, kind: SpanKind) -> None:
    assert operation_kind(operation) is kind


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("LLM", SpanKind.LLM),
        ("EMBEDDING", SpanKind.LLM),
        ("TOOL", SpanKind.TOOL),
        ("CHAIN", SpanKind.CHAIN),
        ("AGENT", SpanKind.CHAIN),
        ("RETRIEVER", SpanKind.RETRIEVAL),
        ("RERANKER", SpanKind.RETRIEVAL),
        ("GUARDRAIL", SpanKind.OTHER),
        ("EVALUATOR", SpanKind.OTHER),
        ("PROMPT", SpanKind.OTHER),
        ("DECISION", SpanKind.OTHER),
        ("UNKNOWN", SpanKind.OTHER),
        ("FOO", SpanKind.OTHER),
        (None, SpanKind.OTHER),
        (7, SpanKind.OTHER),
    ],
)
def test_openinference_kind(value: Any, kind: SpanKind) -> None:
    assert openinference_kind(value) is kind


@pytest.mark.parametrize(
    ("operation_id", "kind"),
    [
        ("ai.generateText.doGenerate", SpanKind.LLM),
        ("ai.streamText.doStream", SpanKind.LLM),
        ("ai.embed.doEmbed", SpanKind.LLM),
        ("ai.toolCall", SpanKind.TOOL),
        ("ai.generateText", SpanKind.CHAIN),
        ("ai.streamText", SpanKind.CHAIN),
        ("ai.embedMany", SpanKind.CHAIN),
        ("ai.rerank", SpanKind.CHAIN),
        ("ai.transcribe", SpanKind.CHAIN),
        (None, SpanKind.OTHER),
        ("", SpanKind.OTHER),
        ("custom.step", SpanKind.OTHER),
    ],
)
def test_vercel_kind(operation_id: str | None, kind: SpanKind) -> None:
    assert vercel_kind(operation_id) is kind


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("workflow", SpanKind.CHAIN),
        ("task", SpanKind.CHAIN),
        ("agent", SpanKind.CHAIN),
        ("tool", SpanKind.TOOL),
        ("unknown", SpanKind.OTHER),
        ("zzz", SpanKind.OTHER),
        (None, SpanKind.OTHER),
        (7, SpanKind.OTHER),
    ],
)
def test_traceloop_kind(value: Any, kind: SpanKind) -> None:
    assert traceloop_kind(value) is kind


def test_kind_tables_ignore_case_and_whitespace() -> None:
    # Review Focus 4: the lookup runs on the stripped, lower-cased text.
    assert openinference_kind(" Chain ") is SpanKind.CHAIN
    assert openinference_kind("llm") is SpanKind.LLM
    assert operation_kind(" Execute_Tool ") is SpanKind.TOOL
    assert vercel_kind(" AI.ToolCall ") is SpanKind.TOOL
    assert vercel_kind("ai.generateText.DoGenerate") is SpanKind.LLM
    assert traceloop_kind(" Workflow ") is SpanKind.CHAIN


def test_infer_kind_http_needs_a_client_span() -> None:
    attributes = {"http.request.method": "GET", "url.full": "https://example.com"}
    assert infer_kind(attributes, client_span=True) is SpanKind.HTTP
    assert infer_kind(attributes, client_span=False) is SpanKind.OTHER
    assert infer_kind({}, client_span=True) is SpanKind.OTHER


def test_infer_kind_db_prefix_is_retrieval() -> None:
    assert infer_kind({"db.system": "postgresql"}, client_span=False) is SpanKind.RETRIEVAL
    assert infer_kind({"db.system": "postgresql"}, client_span=True) is SpanKind.RETRIEVAL
    # An http client span that also touches a database is http: the first rule wins.
    both = {"http.request.method": "GET", "db.system": "redis"}
    assert infer_kind(both, client_span=True) is SpanKind.HTTP


@pytest.mark.parametrize(
    ("convention", "operation", "attributes", "expected"),
    [
        (Convention.GEN_AI, "embeddings", {}, True),
        (Convention.GEN_AI, " Embeddings ", {}, True),
        (Convention.GEN_AI, "chat", {}, False),
        (Convention.GEN_AI, None, {}, False),
        (Convention.GEN_AI, None, {"llm.request.type": "embedding"}, True),
        (Convention.GEN_AI, "chat", {"llm.request.type": "embedding"}, True),
        (Convention.OPENINFERENCE, "embedding", {}, True),
        (Convention.OPENINFERENCE, "EMBEDDING", {}, True),
        (Convention.OPENINFERENCE, "llm", {}, False),
        (Convention.VERCEL_AI, "ai.embed.doEmbed", {}, True),
        (Convention.VERCEL_AI, "ai.embedMany.doEmbed", {}, True),
        (Convention.VERCEL_AI, "ai.embedMany", {}, False),
        (Convention.VERCEL_AI, "ai.generateText.doGenerate", {}, False),
        (Convention.TRACELOOP, "task", {"llm.request.type": "embedding"}, True),
        (Convention.TRACELOOP, "task", {"llm.request.type": " Embedding "}, True),
        (Convention.TRACELOOP, "task", {"llm.request.type": "chat"}, False),
        (Convention.TRACELOOP, "task", {}, False),
        (Convention.NONE, "embeddings", {"llm.request.type": "embedding"}, False),
    ],
)
def test_is_embeddings_operation(
    convention: Convention, operation: str | None, attributes: dict[str, Any], expected: bool
) -> None:
    assert is_embeddings_operation(convention, operation, attributes) is expected
