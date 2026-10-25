"""OTLP spans of every convention, end to end through `POST /v1/otlp/traces` (spec §4, §12).

Each test posts OTLP/JSON and reads the stored rows: the convention mapper, the native fit and
the pipeline all run. The mappers' own tables are tested in `tests/ingest/conventions/`.
"""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import OTLP_CONVENTION_SPANS
from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from tests.gateway_support import logged, metric_delta
from tests.helpers import Workspace, bearer
from tests.ingest.support import otlp_json_document, otlp_json_span, stored_spans

OPENINFERENCE_LLM: dict[str, Any] = {
    "openinference.span.kind": "LLM",
    "llm.system": "openai",
    "llm.model_name": "gpt-4o-2024-08-06",
    "llm.token_count.prompt": 2000,
    "llm.token_count.completion": 100,
    "llm.token_count.prompt_details.cache_read": 1000,
    "llm.input_messages.0.message.role": "user",
    "llm.input_messages.0.message.content": "hello",
}


async def _post(
    client: httpx.AsyncClient, workspace: Workspace, *spans: dict[str, Any]
) -> httpx.Response:
    response = await client.post(
        "/v1/otlp/traces", json=otlp_json_document(*spans), headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200, response.text
    return response


async def _stored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    *spans: dict[str, Any],
) -> dict[str, Span]:
    """Post `spans` (all accepted) and return the stored spans by name."""
    response = await _post(client, workspace, *spans)
    assert response.json() == {}
    return {span.name: span for span in await stored_spans(session_factory)}


async def _stored_trace(session_factory: async_sessionmaker[AsyncSession]) -> Trace:
    async with session_factory() as db:
        await bypass_rls(db)
        return (await db.scalars(select(Trace))).one()


def _child(parent: dict[str, Any], attributes: dict[str, Any], *, name: str) -> dict[str, Any]:
    child = otlp_json_span(attributes, name=name)
    child["traceId"] = parent["traceId"]
    child["parentSpanId"] = parent["spanId"]
    return child


async def test_openinference_llm_span_is_priced_and_kind_llm(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stored = await _stored(
        client, workspace, session_factory, otlp_json_span(OPENINFERENCE_LLM, name="chat")
    )

    span = stored["chat"]
    assert span.kind.value == "llm"
    assert (span.provider, span.model) == ("openai", "gpt-4o-2024-08-06")
    assert (span.input_tokens, span.output_tokens, span.cached_tokens) == (2000, 100, 1000)
    # 1000 uncached * 2.50 + 1000 cached * 1.25 + 100 output * 10.00, per 1M tokens
    assert str(span.cost_usd) == "0.00475000"
    assert span.attributes["spanlight.convention"] == "openinference"
    assert span.attributes["spanlight.operation"] == "llm"
    assert not [key for key in span.attributes if key.startswith("llm.input_messages.")]
    assert span.input == [{"role": "user", "content": "hello"}]


async def test_openinference_tool_and_retriever_kinds(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stored = await _stored(
        client,
        workspace,
        session_factory,
        otlp_json_span({"openinference.span.kind": "TOOL"}, name="tool"),
        otlp_json_span({"openinference.span.kind": "RETRIEVER"}, name="retriever"),
    )

    assert stored["tool"].kind.value == "tool"
    assert stored["tool"].attributes["spanlight.operation"] == "tool"
    assert stored["retriever"].kind.value == "retrieval"
    assert stored["retriever"].attributes["spanlight.operation"] == "retriever"


async def test_vercel_outer_and_inner_spans_store_usage_on_both(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outer = otlp_json_span(
        {
            "ai.operationId": "ai.generateText",
            "ai.model.provider": "openai.chat",
            "ai.model.id": "gpt-4o",
            "ai.usage.promptTokens": 10,
            "ai.usage.completionTokens": 5,
        },
        name="ai.generateText",
    )
    inner = _child(
        outer,
        {
            "ai.operationId": "ai.generateText.doGenerate",
            "ai.model.provider": "openai.chat",
            "ai.model.id": "gpt-4o",
            "gen_ai.response.model": "gpt-4o-2024-08-06",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
        },
        name="ai.generateText.doGenerate",
    )

    stored = await _stored(client, workspace, session_factory, outer, inner)

    chain, llm = stored["ai.generateText"], stored["ai.generateText.doGenerate"]
    assert chain.kind.value == "chain" and (chain.input_tokens, chain.output_tokens) == (10, 5)
    assert llm.kind.value == "llm" and (llm.input_tokens, llm.output_tokens) == (10, 5)
    assert llm.model == "gpt-4o-2024-08-06" and llm.provider == "openai"
    assert chain.attributes["spanlight.convention"] == "vercel_ai"
    assert chain.attributes["spanlight.operation"] == "ai.generateText"


async def test_vercel_provider_suffix_and_ttft(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {
            "ai.operationId": "ai.streamText.doStream",
            "ai.model.provider": "anthropic.messages",
            "ai.model.id": "claude-3-5-haiku-20241022",
            "ai.response.msToFirstChunk": 123.5,
        },
        name="stream",
    )

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["stream"].provider == "anthropic"
    assert stored["stream"].time_to_first_token_ms == 123.5


async def test_traceloop_openai_chat_flattened_prompt_and_finish_reason(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.response.model": "gpt-4o-2024-08-06",
            "llm.request.type": "chat",
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "hello",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "hi!",
            "gen_ai.completion.0.finish_reason": "stop",
            "gen_ai.usage.prompt_tokens": 3,
            "gen_ai.usage.completion_tokens": 2,
        },
        name="openai.chat",
    )

    stored = await _stored(client, workspace, session_factory, span)

    chat = stored["openai.chat"]
    assert chat.attributes["spanlight.convention"] == "gen_ai"
    assert chat.input == [{"role": "user", "content": "hello"}]
    assert chat.output == [{"role": "assistant", "content": "hi!", "finish_reason": "stop"}]
    assert chat.finish_reason == "stop"
    assert (chat.input_tokens, chat.output_tokens) == (3, 2)
    assert not [
        key for key in chat.attributes if key.startswith(("gen_ai.prompt.", "gen_ai.completion."))
    ]


async def test_traceloop_workflow_span_is_chain(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {
            "traceloop.span.kind": "workflow",
            "traceloop.workflow.name": "pipeline",
            "traceloop.entity.input": json.dumps({"q": 1}),
        },
        name="pipeline.workflow",
    )

    stored = await _stored(client, workspace, session_factory, span)

    workflow = stored["pipeline.workflow"]
    assert workflow.kind.value == "chain"
    assert workflow.attributes["spanlight.convention"] == "traceloop"
    assert workflow.attributes["spanlight.operation"] == "workflow"
    assert workflow.input == {"q": 1}
    assert "traceloop.entity.input" not in workflow.attributes
    assert (await _stored_trace(session_factory)).name == "pipeline"


async def test_gen_ai_execute_tool_is_tool_not_llm(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search"}, name="tool"
    )

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["tool"].kind.value == "tool"
    assert stored["tool"].attributes["spanlight.operation"] == "execute_tool"
    assert "usage.unreported_reason" not in stored["tool"].attributes


async def test_gen_ai_tool_call_arguments_and_result_are_the_payloads(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A 100 KB result left in attributes broke the 64 KB attributes cap and rejected the span.
    result = {"page": "x" * 100_000}
    span = otlp_json_span(
        {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "fetch",
            "gen_ai.tool.call.arguments": json.dumps({"url": "https://example.com"}),
            "gen_ai.tool.call.result": json.dumps(result),
        },
        name="tool",
    )

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["tool"].input == {"url": "https://example.com"}
    assert stored["tool"].output == result
    assert "gen_ai.tool.call.arguments" not in stored["tool"].attributes
    assert "gen_ai.tool.call.result" not in stored["tool"].attributes


async def test_embeddings_output_implied_attribute(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {
            "gen_ai.operation.name": "embeddings",
            "gen_ai.system": "openai",
            "gen_ai.request.model": "text-embedding-3-small",
            "gen_ai.usage.input_tokens": 12,
        },
        name="embed",
    )

    stored = await _stored(client, workspace, session_factory, span)

    embed = stored["embed"]
    assert embed.kind.value == "llm"
    assert (embed.input_tokens, embed.output_tokens) == (12, 0)
    assert embed.attributes["usage.output_tokens_implied"] is True
    assert "usage.unreported_reason" not in embed.attributes


async def test_llm_with_model_and_no_usage_gets_not_exported_reason(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span({"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4o"}, name="x")

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["x"].kind.value == "llm" and stored["x"].model == "gpt-4o"
    assert stored["x"].input_tokens is None and stored["x"].output_tokens is None
    assert stored["x"].attributes["usage.unreported_reason"] == "not_exported"


async def test_tool_span_without_usage_has_no_reason(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span({"openinference.span.kind": "TOOL"}, name="tool")

    stored = await _stored(client, workspace, session_factory, span)

    assert "usage.unreported_reason" not in stored["tool"].attributes
    assert "usage.output_tokens_implied" not in stored["tool"].attributes


async def test_litellm_raw_request_child_is_none_other(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = otlp_json_span(
        {"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4o"}, name="litellm_request"
    )
    raw = _child(
        parent,
        {"llm.openai.model": "gpt-4o", "llm.openai.messages": json.dumps([{"role": "user"}])},
        name="raw_gen_ai_request",
    )

    stored = await _stored(client, workspace, session_factory, parent, raw)

    child = stored["raw_gen_ai_request"]
    assert child.kind.value == "other" and child.model is None
    assert child.attributes["spanlight.convention"] == "none"
    assert "spanlight.operation" not in child.attributes
    assert child.attributes["llm.openai.model"] == "gpt-4o"  # kept for inspection, never read


async def test_client_sent_operation_is_dropped_when_the_mapper_has_none(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span({"http.method": "GET", "spanlight.operation": "forged"})

    stored = await _stored(client, workspace, session_factory, span)

    assert "spanlight.operation" not in stored["ok"].attributes


async def test_ttft_numeric_text_is_read_and_junk_is_dropped_counted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A JSON exporter may write the double as text; the attribute is in seconds either way.
    numeric = otlp_json_span(
        {"gen_ai.system": "openai", "gen_ai.client.operation.time_to_first_chunk": "0.42"},
        name="numeric",
    )
    junk = otlp_json_span(
        {"gen_ai.system": "openai", "gen_ai.client.operation.time_to_first_chunk": "soon"},
        name="junk",
    )

    stored = await _stored(client, workspace, session_factory, numeric, junk)

    assert stored["numeric"].time_to_first_token_ms == 420.0
    assert stored["junk"].time_to_first_token_ms is None
    (warning,) = logged(caplog, "otlp_values_dropped")
    assert warning["ttft"] == 1


async def test_openinference_count_as_whole_double_is_an_integer(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = otlp_json_span(
        {"openinference.span.kind": "LLM", "llm.token_count.prompt": 1234.0}, name="chat"
    )

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["chat"].input_tokens == 1234


async def test_exception_message_fills_empty_status_message(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    failed = otlp_json_span({"openinference.span.kind": "LLM", "exception.message": "boom"})
    failed["status"] = {"code": 2}
    told = otlp_json_span(
        {"openinference.span.kind": "LLM", "exception.message": "boom"}, name="told"
    )
    told["status"] = {"code": 2, "message": "rate limited"}

    stored = await _stored(client, workspace, session_factory, failed, told)

    assert stored["ok"].status.value == "error" and stored["ok"].status_message == "boom"
    assert stored["told"].status_message == "rate limited"


async def test_consumed_keys_do_not_spend_the_attribute_limit(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes: dict[str, Any] = {"openinference.span.kind": "LLM"}
    for index in range(300):
        attributes[f"llm.input_messages.{index}.message.content"] = f"m{index}"

    stored = await _stored(client, workspace, session_factory, otlp_json_span(attributes))

    assert len(stored["ok"].input) == 256
    assert stored["ok"].input[0] == {"content": "m0"}
    assert not [key for key in stored["ok"].attributes if key.startswith("llm.input_messages.")]


async def test_openinference_content_keys_are_not_stored_when_capture_is_off(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Ruling C-1: a losing content key must not keep prompt text outside the payload rules.
    patch = await workspace.owner.patch(
        f"/api/v1/projects/{workspace.project_id}", json={"capture_payloads": False}
    )
    assert patch.json()["capture_payloads"] is False
    span = otlp_json_span(
        {
            "openinference.span.kind": "LLM",
            "input.value": json.dumps({"messages": [{"role": "user", "content": "secret"}]}),
            "input.mime_type": "application/json",
            "llm.input_messages.0.message.role": "user",
            "llm.input_messages.0.message.content": "secret",
        }
    )

    stored = await _stored(client, workspace, session_factory, span)

    assert stored["ok"].input is None
    assert "input.value" not in stored["ok"].attributes
    assert "llm.input_messages.0.message.content" not in stored["ok"].attributes
    assert stored["ok"].attributes["input.mime_type"] == "application/json"


async def test_convention_counter_counts_accepted_spans_only(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    accepted = otlp_json_span(OPENINFERENCE_LLM, name="chat")
    rejected = otlp_json_span({"gen_ai.system": "openai"}, name="zero trace id")
    rejected["traceId"] = "00" * 16
    openinference = metric_delta(OTLP_CONVENTION_SPANS, convention="openinference")
    gen_ai = metric_delta(OTLP_CONVENTION_SPANS, convention="gen_ai")

    response = await _post(client, workspace, accepted, rejected)

    assert response.json()["partialSuccess"]["rejectedSpans"] == "1"
    assert openinference.value == 1
    assert gen_ai.value == 0
