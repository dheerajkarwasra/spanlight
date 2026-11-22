"""Google's native JSON/SSE response shape and reported usage."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from typing import Any

from app.gateway.sse import (
    OutputBudget,
    SseFrame,
    StreamSummary,
    Usage,
    as_int,
    as_str,
    dict_at,
    error_text,
    json_object,
)
from app.pricing.tiers import normalize_tier, unknown_tier_spelling


def _count(value: object) -> int | None:
    count = as_int(value)
    return count if count is not None and count >= 0 else None


def google_usage(metadata: Mapping[str, Any], *, blocked: bool = False) -> Usage | None:
    prompt = _count(metadata.get("promptTokenCount"))
    if prompt is None:
        return None
    answer = _count(metadata.get("candidatesTokenCount"))
    thoughts = _count(metadata.get("thoughtsTokenCount")) or 0
    cache = _count(metadata.get("cachedContentTokenCount"))
    raw = as_str(metadata.get("serviceTier"))
    tier = normalize_tier("google", raw)
    return Usage(
        prompt,
        (answer or 0) + thoughts if answer is not None or blocked else None,
        cache if cache is None or cache <= prompt else None,
        service_tier=tier,
        service_tier_raw=unknown_tier_spelling(raw, tier),
    )


def _candidates(body: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    rows = body.get("candidates")
    if isinstance(rows, list):
        yield from (row for row in rows if isinstance(row, dict))


def _blocked(body: Mapping[str, Any]) -> str | None:
    reason = as_str(dict_at(body, "promptFeedback").get("blockReason"))
    return reason if reason and reason != "BLOCK_REASON_UNSPECIFIED" else None


def summarize_gemini(body: dict[str, Any]) -> StreamSummary:
    first = next(_candidates(body), {})
    blocked = _blocked(body)
    content = dict_at(first, "content")
    usage = google_usage(dict_at(body, "usageMetadata"), blocked=blocked is not None)
    return StreamSummary(
        usage=usage,
        model=as_str(body.get("modelVersion")),
        finish_reason=as_str(first.get("finishReason")) or blocked,
        output=content or None,
        response_id=as_str(body.get("responseId")),
        usage_complete=usage is not None and usage.output_tokens is not None,
        saw_output=bool(content),
    )


class GeminiObserver:
    def __init__(self, candidate_count: int) -> None:
        self._count = candidate_count
        self._finished: set[int] = set()
        self._blocked = False
        self._usage: Usage | None = None
        self._final_usage: Usage | None = None
        self._model: str | None = None
        self._response_id: str | None = None
        self._reason: str | None = None
        self._error: str | None = None
        self._parts: list[dict[str, Any]] = []
        self._output = OutputBudget()

    def on_frame(self, frame: SseFrame) -> None:
        body = json_object(frame.data)
        if body is None:
            return
        error = dict_at(body, "error")
        if error:
            self._error = error_text(error.get("status"), error.get("message"))
            return
        self._model = as_str(body.get("modelVersion")) or self._model
        self._response_id = as_str(body.get("responseId")) or self._response_id
        if blocked := _blocked(body):
            self._reason, self._blocked = blocked, True
        for candidate in _candidates(body):
            self._observe_candidate(candidate)
        usage = google_usage(dict_at(body, "usageMetadata"), blocked=self._blocked)
        if usage is not None:
            self._usage = usage
            if self._blocked or len(self._finished) >= self._count:
                self._final_usage = usage

    def _observe_candidate(self, candidate: dict[str, Any]) -> None:
        index = as_int(candidate.get("index")) or 0
        reason = as_str(candidate.get("finishReason"))
        if reason and reason != "FINISH_REASON_UNSPECIFIED" and 0 <= index < self._count:
            self._finished.add(index)
            if index == 0:
                self._reason = reason
        parts = dict_at(candidate, "content").get("parts")
        if index != 0 or not isinstance(parts, list):
            return
        for part in parts:
            if isinstance(part, dict) and self._output.take(json.dumps(part)):
                self._parts.append(part)

    def summary(self) -> StreamSummary:
        final = self._final_usage
        complete = final is not None and final.output_tokens is not None and self._error is None
        usage = (
            final
            if complete
            else (replace(self._usage, output_tokens=None) if self._usage is not None else None)
        )
        return StreamSummary(
            usage=usage,
            model=self._model,
            finish_reason=self._reason,
            output={"role": "model", "parts": self._parts} if self._parts else None,
            response_id=self._response_id,
            output_truncated=self._output.truncated,
            usage_complete=complete,
            saw_output=bool(self._parts),
            error=self._error,
        )
