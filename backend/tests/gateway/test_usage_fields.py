"""The gateway's `Usage`: an unknown output count, the service tier and web search requests.

The bodies are shaped like the providers' documented responses (spec §4): Anthropic's Messages
`usage` with `service_tier` and `server_tool_use`, OpenAI's chat and Responses `usage` with the
tier passed from the top-level `service_tier`. An output count the provider never sent is
`None`, never a placeholder; a tier is stored normalized, with an unknown spelling kept apart.
"""

import json
from typing import Any

import pytest

from app.gateway.cache import usage_from_json, usage_to_json
from app.gateway.response_summary import summarize_response
from app.gateway.sse import Usage
from app.gateway.usage import (
    anthropic_usage,
    chat_usage,
    completed_web_searches,
    responses_usage,
)
from tests.gateway_support import fixture_text

# A Messages `usage` after `message_start` only: the final output count has not come yet.
START_USAGE: dict[str, Any] = {
    "input_tokens": 19,
    "cache_creation_input_tokens": 6404,
    "cache_read_input_tokens": 0,
    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 6404},
}
# A complete Messages `usage` from a batch request that ran two web searches.
SEARCH_USAGE: dict[str, Any] = {
    "input_tokens": 1200,
    "output_tokens": 310,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "service_tier": "batch",
    "server_tool_use": {"web_search_requests": 2},
}
CHAT_USAGE: dict[str, Any] = {
    "prompt_tokens": 19,
    "completion_tokens": 9,
    "total_tokens": 28,
    "prompt_tokens_details": {"cached_tokens": 8, "audio_tokens": 0},
}
RESPONSES_USAGE: dict[str, Any] = {
    "input_tokens": 19,
    "input_tokens_details": {"cached_tokens": 8},
    "output_tokens": 9,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 28,
}


def test_anthropic_usage_with_missing_output_keeps_input() -> None:
    """The input split is known from `message_start`; only the output count is unknown."""
    assert anthropic_usage(START_USAGE) == Usage(
        input_tokens=6423,
        output_tokens=None,
        cached_tokens=0,
        cache_write_tokens=6404,
        cache_write_1h_tokens=6404,
    )


def test_anthropic_usage_without_input_is_unknown() -> None:
    assert anthropic_usage({"output_tokens": 12}) is None
    assert anthropic_usage({}) is None


def test_anthropic_usage_reads_tier_and_web_search() -> None:
    usage = anthropic_usage(SEARCH_USAGE)

    assert usage is not None
    assert (usage.service_tier, usage.web_search_requests) == ("batch", 2)
    assert usage.service_tier_raw is None  # the spelling normalized, so nothing to keep apart


def test_anthropic_usage_keeps_an_unknown_tier_spelling_apart() -> None:
    usage = anthropic_usage({**SEARCH_USAGE, "service_tier": "turbo"})

    assert usage is not None
    assert (usage.service_tier, usage.service_tier_raw) == (None, "turbo")


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("  ", None),
        ("", None),
        ("  Turbo ", "Turbo"),
        pytest.param(" " + "t" * 200 + " ", "t" * 64, id="cut-to-64"),
    ],
)
def test_anthropic_usage_keeps_only_a_trimmed_unknown_tier(raw: str, kept: str | None) -> None:
    """A blank tier says nothing, so nothing is kept apart; a spelling is kept without padding
    and cut to 64 characters, so an upstream behind a custom base URL cannot push a span past
    the attribute limit with it."""
    usage = anthropic_usage({**SEARCH_USAGE, "service_tier": raw})

    assert usage is not None
    assert (usage.service_tier, usage.service_tier_raw) == (None, kept)


def test_chat_usage_normalizes_the_openai_tier() -> None:
    """Chat completions carry the tier at the top level; the caller passes it in."""
    assert chat_usage(CHAT_USAGE) == Usage(19, 9, 8)
    with_tier = chat_usage(CHAT_USAGE, service_tier="default")
    assert with_tier is not None
    assert (with_tier.service_tier, with_tier.service_tier_raw) == ("standard", None)
    long = chat_usage(CHAT_USAGE, service_tier="x" * 65)
    assert long is not None
    assert (long.service_tier, long.service_tier_raw) == (None, "x" * 64)


def test_chat_usage_with_missing_completion_count_keeps_prompt() -> None:
    usage = chat_usage({"prompt_tokens": 19, "prompt_tokens_details": {"cached_tokens": 8}})
    assert usage == Usage(19, None, 8)


def test_responses_usage_carries_tier_and_web_search_count() -> None:
    usage = responses_usage(RESPONSES_USAGE, service_tier="flex", web_search_requests=3)

    assert usage == Usage(19, 9, 8, service_tier="flex", web_search_requests=3)


def test_openai_usage_reads_cache_writes() -> None:
    """GPT-5.6 and later report cache writes in the details, inside the input count."""
    details = {"cached_tokens": 12_000, "cache_write_tokens": 3_000}
    chat = chat_usage(
        {"prompt_tokens": 15_000, "completion_tokens": 9, "prompt_tokens_details": details}
    )
    responses = responses_usage(
        {"input_tokens": 15_000, "output_tokens": 9, "input_tokens_details": details}
    )

    assert chat == responses == Usage(15_000, 9, 12_000, 3_000)


def test_usage_json_round_trip_with_new_fields_and_old_entries() -> None:
    """The cache stores every field; an entry written before a field existed reads it as unknown."""
    full = Usage(
        input_tokens=6423,
        output_tokens=None,
        cached_tokens=0,
        cache_write_tokens=6404,
        cache_write_1h_tokens=6404,
        service_tier="batch",
        web_search_requests=2,
        service_tier_raw=None,
    )
    stored = usage_to_json(full)
    assert stored["output_tokens"] is None
    assert (stored["service_tier"], stored["web_search_requests"]) == ("batch", 2)
    assert usage_from_json(stored) == full

    old = {"input_tokens": 6423, "output_tokens": 12, "cached_tokens": 0}
    assert usage_from_json(old) == Usage(6423, 12, 0, None, None, None, None, None)


def test_completed_web_searches_is_unknown_without_search_items() -> None:
    """No `web_search_call` item: no count was reported (no fee line), not a count of 0."""
    message = {"id": "msg_1", "type": "message", "status": "completed", "content": []}
    failed = {"id": "ws_1", "type": "web_search_call", "status": "failed"}

    assert completed_web_searches([message]) is None
    assert completed_web_searches([]) is None
    assert completed_web_searches([failed, message]) == 0


def test_responses_body_without_search_items_has_no_search_count() -> None:
    body = json.loads(fixture_text("openai/responses.json"))

    summary = summarize_response("responses", json.dumps(body).encode())

    assert summary.usage is not None
    assert summary.usage.web_search_requests is None
