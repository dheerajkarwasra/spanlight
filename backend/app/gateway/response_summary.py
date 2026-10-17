"""What a complete (non-streaming) provider response says: usage, model, finish reason, output.

Pure: bytes in, a `StreamSummary` out, in the same shape the stream observers produce, so a span
looks the same whether the call streamed or not. A body that is not the JSON the surface
documents gives an empty summary; nothing here raises.
"""

from typing import Any, NamedTuple

from app.gateway.errors import Surface
from app.gateway.sse import StreamSummary, Usage, as_str, dict_at, json_object
from app.gateway.usage import (
    anthropic_usage,
    chat_usage,
    completed_web_searches,
    responses_usage,
)


def summarize_response(surface: Surface, body: bytes) -> StreamSummary:
    """The summary of a provider's JSON answer on `surface`."""
    parsed = _parse(body)
    if parsed is None:
        return StreamSummary()
    if surface == "chat_completions":
        return _chat(parsed)
    if surface == "responses":
        return _responses(parsed)
    if surface == "messages":
        return _messages(parsed)
    return StreamSummary(model=as_str(parsed.get("model")))


class UpstreamErrorDetail(NamedTuple):
    """What a provider's error envelope says; each field None when the body does not say."""

    type: str | None
    code: str | None
    message: str | None


def upstream_error_detail(body: bytes) -> UpstreamErrorDetail:
    """The provider's error type, code and message, read from either envelope.

    The type is `error.type` in both. The code is OpenAI's `error.code`, else Anthropic's
    `error.details.error_code` (its only documented one is `enforced_spend_limit_reached`).
    """
    parsed = _parse(body)
    error = dict_at(parsed, "error")
    code = as_str(error.get("code")) or as_str(dict_at(error, "details").get("error_code"))
    return UpstreamErrorDetail(
        type=as_str(error.get("type")), code=code, message=as_str(error.get("message"))
    )


def _parse(body: bytes) -> dict[str, Any] | None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return json_object(text)


def _chat(body: dict[str, Any]) -> StreamSummary:
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    first = first if isinstance(first, dict) else {}
    message = dict_at(first, "message")
    return _summary(
        usage=chat_usage(dict_at(body, "usage"), service_tier=as_str(body.get("service_tier"))),
        model=as_str(body.get("model")),
        finish_reason=as_str(first.get("finish_reason")),
        output=message or None,
        response_id=as_str(body.get("id")),
    )


def _responses(body: dict[str, Any]) -> StreamSummary:
    output = body.get("output")
    items = output if isinstance(output, list) else None
    return _summary(
        usage=responses_usage(
            dict_at(body, "usage"),
            service_tier=as_str(body.get("service_tier")),
            web_search_requests=completed_web_searches(items) if items is not None else None,
        ),
        model=as_str(body.get("model")),
        finish_reason=as_str(body.get("status")),
        output={"output": items} if items is not None else None,
        response_id=as_str(body.get("id")),
    )


def _messages(body: dict[str, Any]) -> StreamSummary:
    content = body.get("content")
    return _summary(
        usage=anthropic_usage(dict_at(body, "usage")),
        model=as_str(body.get("model")),
        finish_reason=as_str(body.get("stop_reason")),
        output={"role": "assistant", "content": content} if isinstance(content, list) else None,
        response_id=as_str(body.get("id")),
    )


def _summary(
    *,
    usage: Usage | None,
    model: str | None,
    finish_reason: str | None,
    output: dict[str, Any] | None,
    response_id: str | None,
) -> StreamSummary:
    """A complete answer's usage is final when it carries both counts; absent, it is unknown."""
    return StreamSummary(
        usage=usage,
        model=model,
        finish_reason=finish_reason,
        output=output,
        response_id=response_id,
        usage_complete=usage is not None and usage.output_tokens is not None,
        saw_output=output is not None,
    )
