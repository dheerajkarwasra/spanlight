"""Reading OpenAI Chat Completions and Responses objects onto spans.

Shared by the non-streaming endpoints, the stream accumulators and the batch
output reader, so a response is recorded the same way wherever it arrives.
"""

from __future__ import annotations

from typing import Any

from .._span import Span
from ._common import as_int, get_field


def chat_output(response: Any) -> tuple[Any, list[Any]]:
    """A chat completion's output (one message, or a list of them) and its finish reasons."""
    choices = get_field(response, "choices") or []
    messages = [get_field(choice, "message") for choice in choices]
    finish_reasons = [get_field(choice, "finish_reason") for choice in choices]
    return (messages[0] if len(messages) == 1 else messages), finish_reasons


def first_str(values: list[Any]) -> str | None:
    """Return the first value if it is a string (the first choice's reason)."""
    return values[0] if values and isinstance(values[0], str) else None


def record_chat_usage(span: Span, usage: Any) -> None:
    if usage is None:
        return
    span.set_usage(**usage_fields(usage))
    reasoning = as_int(get_field(usage, "completion_tokens_details", "reasoning_tokens"))
    if reasoning:
        span.set_attribute("usage.reasoning_tokens", reasoning)


def usage_fields(usage: Any) -> dict[str, Any]:
    """Chat or Responses ``usage`` as :meth:`Span.set_usage` keywords.

    Chat completions name the counts ``prompt_tokens`` / ``completion_tokens``; the
    Responses API names them ``input_tokens`` / ``output_tokens``. Both include cached
    tokens in the prompt count, as Spanlight does.
    """
    if get_field(usage, "prompt_tokens") is not None:
        return {
            "input_tokens": as_int(get_field(usage, "prompt_tokens")),
            "output_tokens": as_int(get_field(usage, "completion_tokens")),
            "cached_tokens": as_int(get_field(usage, "prompt_tokens_details", "cached_tokens")),
        }
    return {
        "input_tokens": as_int(get_field(usage, "input_tokens")),
        "output_tokens": as_int(get_field(usage, "output_tokens")),
        "cached_tokens": as_int(get_field(usage, "input_tokens_details", "cached_tokens")),
    }


def record_service_tier(span: Span, response: Any) -> None:
    """Record the top-level ``service_tier`` OpenAI answered with (not inside ``usage``)."""
    tier = get_field(response, "service_tier")
    if isinstance(tier, str):
        span.set_usage(service_tier=tier)


def web_search_calls(response: Any) -> int | None:
    """Billed web searches of a Responses object: its completed ``web_search_call`` items.

    ``None`` when the output holds no search item at all, so a call that never searched
    sends no count; a call whose searches all failed sends ``0``.
    """
    searches = [
        item
        for item in get_field(response, "output") or []
        if get_field(item, "type") == "web_search_call"
    ]
    if not searches:
        return None
    return sum(1 for item in searches if get_field(item, "status") == "completed")


def record_response_id(span: Span, response: Any) -> None:
    response_id = get_field(response, "id")
    if isinstance(response_id, str):
        span.set_attribute("response.id", response_id)


def responses_output(response: Any) -> dict[str, Any]:
    """Summarize a ``Response`` as an assistant message plus any non-text items."""
    text_parts: list[str] = []
    other_items: list[Any] = []
    for item in get_field(response, "output") or []:
        if get_field(item, "type") != "message":
            other_items.append(item)
            continue
        for part in get_field(item, "content") or []:
            text = get_field(part, "text")
            if isinstance(text, str):
                text_parts.append(text)

    output: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if other_items:
        output["items"] = other_items
    return output
