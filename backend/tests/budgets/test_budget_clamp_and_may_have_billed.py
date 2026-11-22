"""Pure unit tests for gateway output-cap clamping and multi-attempt billing fallback."""

from app.budgets.reserve import _may_have_billed, has_output_cap
from app.db.models.gateway import ProviderKind
from app.gateway.attempts import _clamp_cap_key


def test_has_output_cap_detects_positive_token_limits() -> None:
    assert has_output_cap({"max_tokens": 100}) is True
    assert has_output_cap({"max_completion_tokens": 256}) is True
    assert has_output_cap({"max_output_tokens": 512}) is True
    assert has_output_cap({"model": "gpt-5", "messages": []}) is False
    assert has_output_cap({"max_tokens": None}) is False
    assert has_output_cap({"max_tokens": 0}) is False
    assert has_output_cap({"max_tokens": True}) is False


def test_clamp_cap_key_matches_surface_and_provider() -> None:
    assert _clamp_cap_key("messages", ProviderKind.ANTHROPIC) == "max_tokens"
    assert _clamp_cap_key("responses", ProviderKind.OPENAI) == "max_output_tokens"
    assert _clamp_cap_key("chat_completions", ProviderKind.OPENAI) == "max_completion_tokens"
    assert _clamp_cap_key("chat_completions", ProviderKind.OPENAI_COMPATIBLE) == "max_tokens"
    assert _clamp_cap_key("models", ProviderKind.OPENAI) is None


def test_may_have_billed_handles_explicit_flag_and_attempt_log() -> None:
    assert _may_have_billed({"attributes": {"spanlight.gateway.may_have_billed": True}}) is True
    assert _may_have_billed({"attributes": {"spanlight.gateway.may_have_billed": False}}) is False
    assert (
        _may_have_billed(
            {
                "attributes": {
                    "spanlight.gateway.attempt_log": [
                        {"status": None, "error": "timeout"},
                        {"status": 400, "error": None},
                    ]
                }
            }
        )
        is True
    )
    assert (
        _may_have_billed(
            {
                "attributes": {
                    "spanlight.gateway.attempt_log": [
                        {"status": 429, "error": None},
                        {"status": 400, "error": None},
                    ]
                }
            }
        )
        is False
    )
