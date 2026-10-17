"""The registry: detect, map, fill gaps from `gen_ai.*`, apply the embeddings rule (spec §4.3)."""

from app.db.models.telemetry import SpanKind
from app.ingest.conventions import Convention, map_fields
from tests.ingest.conventions.test_detect import ALL_MARKERS


def test_openinference_fields_win_and_gen_ai_fills_usage() -> None:
    attributes = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "gpt-4.1",
        "gen_ai.request.model": "gpt-4.1-mini",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 4,
    }
    convention, fields = map_fields(attributes, client_span=False)
    assert convention is Convention.OPENINFERENCE
    assert fields.model == "gpt-4.1"
    assert fields.usage.input_tokens == 10
    assert fields.usage.output_tokens == 4
    assert fields.kind is SpanKind.LLM


def test_vercel_inner_span_gets_gen_ai_response_model_when_ai_response_model_absent() -> None:
    attributes = {
        "ai.operationId": "ai.generateText.doGenerate",
        "gen_ai.response.model": "gpt-4.1-2025-04-14",
        "gen_ai.usage.input_tokens": 3,
    }
    convention, fields = map_fields(attributes, client_span=True)
    assert convention is Convention.VERCEL_AI
    assert fields.model == "gpt-4.1-2025-04-14"
    assert fields.usage.input_tokens == 3
    assert fields.kind is SpanKind.LLM


def test_traceloop_workflow_span_stays_without_model() -> None:
    attributes = {"traceloop.span.kind": "workflow", "traceloop.workflow.name": "pipeline"}
    convention, fields = map_fields(attributes, client_span=False)
    assert convention is Convention.TRACELOOP
    assert fields.model is None
    assert fields.usage.input_tokens is None
    assert fields.kind is SpanKind.CHAIN
    assert fields.trace_name == "pipeline"


def test_every_marker_at_once_is_openinference() -> None:
    convention, fields = map_fields(ALL_MARKERS, client_span=False)
    assert convention is Convention.OPENINFERENCE
    assert fields.kind is SpanKind.LLM
    assert fields.provider == "openai"  # filled from `gen_ai.system`


def test_none_convention_uses_infer_kind() -> None:
    convention, fields = map_fields({"db.statement": "SELECT 1"}, client_span=False)
    assert convention is Convention.NONE
    assert fields.kind is SpanKind.RETRIEVAL
    assert fields.consumed_keys == frozenset()
    _, http = map_fields({"http.method": "GET"}, client_span=True)
    assert http.kind is SpanKind.HTTP


def test_embeddings_imply_zero_output_tokens_with_flag() -> None:
    attributes = {"gen_ai.operation.name": "embeddings", "gen_ai.usage.input_tokens": 12}
    convention, fields = map_fields(attributes, client_span=False)
    assert convention is Convention.GEN_AI
    assert fields.usage.input_tokens == 12
    assert fields.usage.output_tokens == 0
    assert fields.output_tokens_implied is True


def test_embeddings_without_input_imply_nothing() -> None:
    _, fields = map_fields({"gen_ai.operation.name": "embeddings"}, client_span=False)
    assert fields.usage.output_tokens is None
    assert fields.output_tokens_implied is False


def test_reported_output_tokens_are_not_overridden() -> None:
    attributes = {
        "gen_ai.operation.name": "embeddings",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 5,
    }
    _, fields = map_fields(attributes, client_span=False)
    assert fields.usage.output_tokens == 5
    assert fields.output_tokens_implied is False


def test_vercel_do_embed_and_openinference_embedding_imply_too() -> None:
    _, vercel = map_fields(
        {"ai.operationId": "ai.embed.doEmbed", "ai.usage.inputTokens": 7}, client_span=True
    )
    assert (vercel.usage.output_tokens, vercel.output_tokens_implied) == (0, True)
    _, openinference = map_fields(
        {"openinference.span.kind": "EMBEDDING", "llm.token_count.prompt": 9}, client_span=False
    )
    assert (openinference.usage.output_tokens, openinference.output_tokens_implied) == (0, True)


def test_dropped_messages_are_summed_across_mapper_and_fallback() -> None:
    attributes = {"openinference.span.kind": "LLM"}
    for index in range(300):
        attributes[f"llm.input_messages.{index}.message.role"] = "user"
        attributes[f"gen_ai.prompt.{index}.role"] = "user"
    _, fields = map_fields(attributes, client_span=False)
    assert fields.dropped_messages == 88  # 44 past the 256-index limit, from each mapper
    assert "llm.input_messages.0.message.role" in fields.consumed_keys
    assert "gen_ai.prompt.0.role" in fields.consumed_keys


def test_primary_metadata_keys_win_over_gen_ai_ones() -> None:
    attributes = {
        "ai.operationId": "ai.generateText",
        "ai.telemetry.metadata.env": "prod",
        "gen_ai.prompt.0.role": "user",
    }
    _, fields = map_fields(attributes, client_span=False)
    assert fields.metadata == {"env": "prod"}
    assert "ai.telemetry.metadata.env" in fields.consumed_keys
    assert "gen_ai.prompt.0.role" in fields.consumed_keys
