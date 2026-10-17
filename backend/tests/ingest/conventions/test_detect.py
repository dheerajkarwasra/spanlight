"""Convention detection: the five rules of the Bet 4 spec, in priority order.

A convention's own marker key (`openinference.span.kind`, `ai.operationId`, `traceloop.span.kind`)
identifies its spans; `gen_ai.*` is the vocabulary everyone borrows, so it is the fallback.
"""

from app.ingest.conventions import Convention, detect
from app.ingest.conventions.read import as_text, first, has_prefix

ALL_MARKERS = {
    "openinference.span.kind": "LLM",
    "ai.operationId": "ai.generateText",
    "ai.model.id": "gpt-4.1",
    "traceloop.span.kind": "workflow",
    "gen_ai.system": "openai",
}


def test_openinference_marker_wins_over_every_other_marker() -> None:
    assert detect(ALL_MARKERS) is Convention.OPENINFERENCE


def test_vercel_by_operation_id() -> None:
    assert detect({"ai.operationId": "ai.generateText"}) is Convention.VERCEL_AI


def test_vercel_by_ai_model_id_with_an_ai_key() -> None:
    assert detect({"ai.model.id": "gpt-4.1", "ai.settings.maxRetries": 2}) is Convention.VERCEL_AI


def test_ai_prefix_without_model_id_is_not_vercel() -> None:
    assert detect({"ai.foo": "bar"}) is Convention.NONE


def test_traceloop_marker() -> None:
    assert detect({"traceloop.span.kind": "workflow", "gen_ai.system": "openai"}) is (
        Convention.TRACELOOP
    )


def test_gen_ai_prefix_is_the_fallback() -> None:
    assert detect({"gen_ai.usage.input_tokens": 3}) is Convention.GEN_AI


def test_traceloop_provider_span_without_kind_is_gen_ai() -> None:
    assert detect({"gen_ai.system": "openai", "llm.request.type": "chat"}) is Convention.GEN_AI


def test_empty_attributes_are_none() -> None:
    assert detect({}) is Convention.NONE


def test_llm_openai_echo_keys_alone_are_none() -> None:
    assert detect({"llm.openai.messages": "[]"}) is Convention.NONE


def test_first_skips_empty_strings_and_none() -> None:
    attributes = {"a": "", "b": None, "c": 0, "d": "x"}
    assert first(attributes, "a", "b", "c", "d") == 0
    assert first(attributes, "a", "b") is None
    assert first(attributes, "missing") is None


def test_as_text_stringifies_numbers() -> None:
    assert as_text(42) == "42"
    assert as_text(0.42) == "0.42"
    assert as_text("x") == "x"
    assert as_text(None) is None


def test_has_prefix_matches_a_leading_key_segment() -> None:
    assert has_prefix({"ai.model.id": "m"}, "ai.")
    assert not has_prefix({"openai.model": "m"}, "ai.")
    assert not has_prefix({}, "ai.")
