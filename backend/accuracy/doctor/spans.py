"""Synthetic span builders: native-ingest spans with realistic payloads. Pure.

Used only where no real traffic can produce the condition (see the README); every builder
returns a dict that `app.ingest.schemas.SpanIn` accepts (`tests/accuracy/test_synthetic.py`).
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from accuracy.doctor.common import PROVIDER, new_span_id, new_trace_id

# Anthropic's spend-cap refusal (platform.claude.com/docs/en/api/rate-limits): a 429
# `rate_limit_error` whose `details.error_code` is `enforced_spend_limit_reached`, no retry-after.
SPEND_CAP_ERROR_TYPE = "rate_limit_error"
SPEND_CAP_ERROR_CODE = "enforced_spend_limit_reached"
SPEND_CAP_MESSAGE = (
    "429 rate_limit_error: You have reached your API usage limits: your organization has "
    "crossed its monthly API usage threshold."
)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def llm_span(
    *,
    started: datetime,
    duration_ms: int,
    model: str,
    environment: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    provider: str | None = PROVIDER,
    status: str = "ok",
    status_message: str | None = None,
    http_status: int | None = None,
    cached_tokens: int | None = None,
    finish_reason: str | None = "end_turn",
    session_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
    prompt: str = "Summarise the last three invoices for this customer.",
    attributes: Mapping[str, Any] | None = None,
    usage: bool = True,
) -> dict[str, Any]:
    """One native-ingest `llm` span (see `app/ingest/schemas.py`)."""
    ended = started + timedelta(milliseconds=duration_ms)
    attrs: dict[str, Any] = {"request.stream": False}
    if http_status is not None:
        attrs["http.status_code"] = http_status
    if attributes:
        attrs.update(attributes)
    span: dict[str, Any] = {
        "trace_id": trace_id or new_trace_id(),
        "span_id": new_span_id(),
        "name": f"chat {model}",
        "kind": "llm",
        "status": status,
        "status_message": status_message,
        "start_time": iso(started),
        "end_time": iso(ended),
        "provider": provider,
        "model": model,
        "input": {
            "model": model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        },
        "attributes": attrs,
        "trace": {
            "name": "assistant.reply",
            "environment": environment,
            "session_id": session_id,
            "user_id": user_id,
        },
    }
    if usage:
        span["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
        }
    if status == "ok":
        span["output"] = {
            "role": "assistant",
            "content": [{"type": "text", "text": "The three most recent invoices are settled."}],
        }
        if finish_reason is not None:
            span["finish_reason"] = finish_reason
    return span


def spend_cap_attributes() -> dict[str, Any]:
    """The `provider.*` attributes the gateway and the SDK record for Anthropic's spend cap."""
    return {
        "provider.error.type": SPEND_CAP_ERROR_TYPE,
        "provider.error.code": SPEND_CAP_ERROR_CODE,
    }


def tool_span(
    *,
    started: datetime,
    duration_ms: int,
    name: str,
    tool_input: Any,
    trace_id: str,
    environment: str | None,
    parent_span_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    ended = started + timedelta(milliseconds=duration_ms)
    return {
        "trace_id": trace_id,
        "span_id": new_span_id(),
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": "tool",
        "status": "ok",
        "start_time": iso(started),
        "end_time": iso(ended),
        "input": tool_input,
        "output": {"ok": True},
        "attributes": {},
        "trace": {"environment": environment, "session_id": session_id},
    }


def spread(start: datetime, end: datetime, count: int) -> list[datetime]:
    """`count` start times evenly spread over `[start, end)`, oldest first."""
    if count <= 0:
        return []
    step = (end - start) / count
    return [start + step * i for i in range(count)]


def marked(index: int, marked_count: int, total: int) -> bool:
    """Exactly `marked_count` of `total` indexes, spread evenly (index * marked crosses an
    integer)."""
    return (index * marked_count) // total != ((index + 1) * marked_count) // total
