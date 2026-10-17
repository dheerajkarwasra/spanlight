"""Span kind tables for every convention (spec §4.4).

No new ``SpanKind`` value: each exporter kind maps onto the existing six, and the finer value
is kept as ``spanlight.operation`` by the mappers. Every lookup runs on the stripped,
lower-cased text so ``"llm"``, ``" Chain "`` and ``7`` resolve the same way as their canonical
spellings (an unknown value is ``other``).
"""

from collections.abc import Mapping
from typing import Any

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.types import Convention

_GEN_AI_OPERATIONS: dict[str, SpanKind] = {
    "chat": SpanKind.LLM,
    "text_completion": SpanKind.LLM,
    "generate_content": SpanKind.LLM,
    "embeddings": SpanKind.LLM,
    "execute_tool": SpanKind.TOOL,
    "invoke_agent": SpanKind.CHAIN,
    "create_agent": SpanKind.CHAIN,
    "invoke_workflow": SpanKind.CHAIN,
    "retrieval": SpanKind.RETRIEVAL,
}

_OPENINFERENCE_KINDS: dict[str, SpanKind] = {
    "llm": SpanKind.LLM,
    "embedding": SpanKind.LLM,
    "tool": SpanKind.TOOL,
    "chain": SpanKind.CHAIN,
    "agent": SpanKind.CHAIN,
    "retriever": SpanKind.RETRIEVAL,
    "reranker": SpanKind.RETRIEVAL,
}

_VERCEL_PROVIDER_SUFFIXES = (".dogenerate", ".dostream", ".doembed")
_VERCEL_TOOL_CALL = "ai.toolcall"

_TRACELOOP_KINDS: dict[str, SpanKind] = {
    "workflow": SpanKind.CHAIN,
    "task": SpanKind.CHAIN,
    "agent": SpanKind.CHAIN,
    "tool": SpanKind.TOOL,
}


def _normalize(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _lookup(table: Mapping[str, SpanKind], value: Any, default: SpanKind) -> SpanKind:
    return table.get(_normalize(value), default)


def operation_kind(operation_name: str | None) -> SpanKind:
    """Kind of a ``gen_ai.*`` span.

    An absent (``None``) operation name is ``llm``, today's rule for older instrumentations;
    an empty or unknown value is ``other``.
    """
    if operation_name is None:
        return SpanKind.LLM
    return _lookup(_GEN_AI_OPERATIONS, operation_name, SpanKind.OTHER)


def openinference_kind(value: Any) -> SpanKind:
    """Kind of an OpenInference span from ``openinference.span.kind``."""
    return _lookup(_OPENINFERENCE_KINDS, value, SpanKind.OTHER)


def vercel_kind(operation_id: str | None) -> SpanKind:
    """Kind of a Vercel AI SDK span from ``ai.operationId``.

    The inner provider call (``.doGenerate``, ``.doStream``, ``.doEmbed``) is the model call;
    ``ai.toolCall`` is a tool; any other ``ai.*`` operation is the outer step, a chain.
    """
    operation = _normalize(operation_id)
    if operation.endswith(_VERCEL_PROVIDER_SUFFIXES):
        return SpanKind.LLM
    if operation == _VERCEL_TOOL_CALL:
        return SpanKind.TOOL
    if operation.startswith("ai."):
        return SpanKind.CHAIN
    return SpanKind.OTHER


def traceloop_kind(value: Any) -> SpanKind:
    """Kind of a Traceloop span from ``traceloop.span.kind``."""
    return _lookup(_TRACELOOP_KINDS, value, SpanKind.OTHER)


def infer_kind(attributes: Mapping[str, Any], *, client_span: bool) -> SpanKind:
    """Kind of a span with no convention: ``http`` for a client span, ``retrieval`` via ``db.``."""
    keys = attributes.keys()
    if client_span and any(key.startswith(("http.", "url.")) for key in keys):
        return SpanKind.HTTP
    if any(key.startswith("db.") for key in keys):
        return SpanKind.RETRIEVAL
    return SpanKind.OTHER


def is_embeddings_operation(
    convention: Convention, operation: str | None, attributes: Mapping[str, Any]
) -> bool:
    """Whether the span is an embeddings call, which produces no output tokens (spec §4.3).

    Traceloop's provider spans carry ``llm.request.type`` but no ``traceloop.span.kind``, so
    they detect as ``gen_ai``; the ``gen_ai`` branch reads that attribute too.
    """
    value = _normalize(operation)
    match convention:
        case Convention.GEN_AI:
            return value == "embeddings" or _traceloop_embedding(attributes)
        case Convention.OPENINFERENCE:
            return value == "embedding"
        case Convention.VERCEL_AI:
            return value.endswith(".doembed")
        case Convention.TRACELOOP:
            return _traceloop_embedding(attributes)
        case _:
            return False


def _traceloop_embedding(attributes: Mapping[str, Any]) -> bool:
    return _normalize(attributes.get("llm.request.type")) == "embedding"
