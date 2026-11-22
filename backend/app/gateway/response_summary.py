"""What a complete (non-streaming) provider response says: usage, model, finish reason, output.

Pure: bytes in, a `StreamSummary` out, in the same shape the stream observers produce, so a span
looks the same whether the call streamed or not. A body that is not the JSON the surface
documents gives an empty summary; nothing here raises.
"""

from typing import Any, NamedTuple

from app.db.models import ProviderKind
from app.gateway.errors import Surface
from app.gateway.gemini_response import summarize_gemini
from app.gateway.sse import StreamSummary, Usage, as_str, dict_at, json_object
from app.gateway.usage import (
    anthropic_usage,
    chat_usage,
    completed_web_searches,
    responses_usage,
)


def summarize_response(
    surface: Surface, body: bytes, *, provider: ProviderKind | None = None
) -> StreamSummary:
    """The summary of a provider's JSON answer on `surface`."""
    parsed = _parse(body)
    if parsed is None:
        return StreamSummary()
    if surface == "chat_completions":
        return _chat(parsed, provider)
    if surface == "responses":
        return _responses(parsed, provider)
    if surface == "messages":
        return _messages(parsed)
    if surface == "embeddings":
        return _embeddings(parsed)
    if surface == "generate_content":
        return summarize_gemini(parsed)
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
    parsed = _parse(body) or {}
    error = dict_at(parsed, "error")
    if not error and parsed.get("object") == "error":
        error = parsed
    code = as_str(error.get("code")) or as_str(dict_at(error, "details").get("error_code"))
    status = as_str(error.get("status"))
    return UpstreamErrorDetail(
        type=as_str(error.get("type")) or status,
        code=code or status,
        message=as_str(error.get("message")) or as_str(parsed.get("detail")),
    )


def _parse(body: bytes) -> dict[str, Any] | None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return json_object(text)


def _chat(body: dict[str, Any], provider: ProviderKind | None) -> StreamSummary:
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    first = first if isinstance(first, dict) else {}
    message = dict_at(first, "message")
    return _summary(
        usage=chat_usage(
            dict_at(body, "usage"), service_tier=as_str(body.get("service_tier")), provider=provider
        ),
        model=as_str(body.get("model")),
        finish_reason=as_str(first.get("finish_reason")),
        output=message or None,
        response_id=as_str(body.get("id")),
    )


def _responses(body: dict[str, Any], provider: ProviderKind | None) -> StreamSummary:
    output = body.get("output")
    items = output if isinstance(output, list) else None
    return _summary(
        usage=responses_usage(
            dict_at(body, "usage"),
            service_tier=as_str(body.get("service_tier")),
            web_search_requests=completed_web_searches(items) if items is not None else None,
            provider=provider,
        ),
        model=as_str(body.get("model")),
        finish_reason=responses_finish_reason(body),
        output={"output": items} if items is not None else None,
        response_id=as_str(body.get("id")),
    )


def responses_finish_reason(body: dict[str, Any]) -> str | None:
    status = as_str(body.get("status"))
    if status == "incomplete":
        return as_str(dict_at(body, "incomplete_details").get("reason")) or status
    return status


def _messages(body: dict[str, Any]) -> StreamSummary:
    content = body.get("content")
    return _summary(
        usage=anthropic_usage(dict_at(body, "usage")),
        model=as_str(body.get("model")),
        finish_reason=as_str(body.get("stop_reason")),
        output={"role": "assistant", "content": content} if isinstance(content, list) else None,
        response_id=as_str(body.get("id")),
    )


def _embeddings(body: dict[str, Any]) -> StreamSummary:
    usage_obj = dict_at(body, "usage")
    prompt = usage_obj.get("prompt_tokens") or usage_obj.get("total_tokens")
    usage = (
        Usage(input_tokens=prompt, output_tokens=0, cached_tokens=None)
        if isinstance(prompt, int) and not isinstance(prompt, bool)
        else None
    )
    return _summary(
        usage=usage,
        model=as_str(body.get("model")),
        finish_reason=None,
        output=None,
        response_id=None,
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
