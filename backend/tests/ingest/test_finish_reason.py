"""Finish-reason sources and canonical values: native, gateway, SDK and OTLP vocabularies."""

import pytest

from app.ingest.finish_reason import _ATTRIBUTE_SOURCES, canonical_finish_reason, raw_finish_reason

NEW_SOURCES = (
    "ai.response.finishReason",
    "llm.response.finish_reason",
    "gen_ai.response.finish_reason",
    "gen_ai.completion.0.finish_reason",
    "llm.finish_reason",
)


def test_new_attribute_sources_in_order() -> None:
    assert tuple(key for key, _ in _ATTRIBUTE_SOURCES[4:]) == NEW_SOURCES
    assert all(not is_list for _, is_list in _ATTRIBUTE_SOURCES[4:])
    # Each source yields to every source before it.
    for position, key in enumerate(NEW_SOURCES):
        later = {later_key: "later" for later_key in NEW_SOURCES[position + 1 :]}
        assert raw_finish_reason(None, {**later, key: "first"}) == "first"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("content-filter", "content_filter"),
        ("tool-calls", "tool_calls"),
        ("error", "other"),
        ("unknown", "other"),
        ("SAFETY", "content_filter"),
        ("recitation", "content_filter"),
        ("blocklist", "content_filter"),
        ("malformed_function_call", "other"),
        ("MAX_TOKENS", "length"),
    ],
)
def test_vercel_and_gemini_values_canonical(raw: str, expected: str) -> None:
    assert canonical_finish_reason(raw) == expected


def test_existing_sources_unchanged() -> None:
    assert tuple(_ATTRIBUTE_SOURCES[:4]) == (
        ("finish_reason", False),
        ("response.finish_reasons", True),
        ("response.stop_reason", False),
        ("gen_ai.response.finish_reasons", True),
    )
    assert raw_finish_reason("declared", {"finish_reason": "x"}) == "declared"
    assert raw_finish_reason(None, {"response.finish_reasons": ["length"]}) == "length"
    assert raw_finish_reason(None, {"gen_ai.response.finish_reasons": []}) is None
    assert canonical_finish_reason("end_turn") == "stop"
    assert canonical_finish_reason(None) is None
    assert canonical_finish_reason("something_new") == "other"
