"""The request behind "Explain with Claude": the prompt for one insight and its trace excerpts.

Pure: no database, no clock, no I/O. `build_explain_request` turns an insight, its catalogue
entry and up to `MAX_EXCERPTS` trace excerpts into the gateway request the explain service sends.
Every string taken from the project (the insight's copy and evidence, the spans' fields and
payloads) passes through `redact_text` or `redact_json` before it is added, and each excerpt is
cut to `MAX_EXCERPT_CHARS`, its payloads to `MAX_PAYLOAD_CHARS` each. A project that does not
capture payloads has none to add.

`worst_case_input_tokens` is the budget's estimate of the prompt's size (`app.pricing.tokens`:
3 ASCII characters per token, every other character one token, the rule the LLM judges use), and
`answer_text` reads the text of the model's answer.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.redact import redact_json, redact_text
from app.db.models import Insight
from app.gateway.context import GatewayRequest
from app.gateway.trace_headers import TraceHeaders
from app.insights.catalogue import KindInfo
from app.pricing.tokens import estimate_text_tokens

SYSTEM_PROMPT = (
    "You are the Spanlight Doctor. Explain the finding below to the engineer who owns this "
    "application: the most likely cause given the evidence, how to confirm it in the listed "
    "traces, and a concrete fix. Use only the evidence given; say so when the evidence is not "
    "enough. Under 200 words, plain text, no headings."
)

EXPLAIN_TAG = "doctor-explain"
MAX_OUTPUT_TOKENS = 600
MAX_EXCERPTS = 5
MAX_EXCERPT_CHARS = 2048
MAX_PAYLOAD_CHARS = 1024
MAX_STATUS_MESSAGE_CHARS = 300
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TraceExcerpt:
    """The span that best shows what happened in one cited trace (its failed llm call when it
    has one). `None` means the span did not say; `input` and `output` are `None` when the
    project does not capture payloads."""

    trace_id: str
    model: str | None
    status: str
    error_class: str | None
    status_message: str | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    input: Any
    output: Any


def build_explain_request(
    insight: Insight,
    info: KindInfo | None,
    excerpts: Sequence[TraceExcerpt],
    *,
    model: str,
) -> GatewayRequest:
    """The Messages call that asks `model` to explain `insight`.

    `info` is the catalogue entry of the insight's kind (None when the catalogue no longer has
    it). At most `MAX_EXCERPTS` excerpts are used, in the order given. The trace is tagged
    `doctor-explain`; the explain service sets the environment.
    """
    return GatewayRequest(
        surface="messages",
        body={
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message(insight, info, excerpts)}],
        },
        stream=False,
        trace=TraceHeaders(tags=[EXPLAIN_TAG]),
    )


def user_message(insight: Insight, info: KindInfo | None, excerpts: Sequence[TraceExcerpt]) -> str:
    """The finding, its evidence and the trace excerpts, as plain text."""
    evidence = insight.evidence if isinstance(insight.evidence, dict) else {}
    raw_window = evidence.get("window")
    window: dict[str, Any] = raw_window if isinstance(raw_window, dict) else {}
    lines = [
        f"Finding: {_clean(info.label if info is not None else insight.kind)}",
        f"Severity: {_clean(insight.severity.value)}",
        f"Title: {_clean(insight.title)}",
        f"Summary: {_clean(insight.summary)}",
        f"Failure layer: {_clean(insight.failure_layer)}",
        f"Certainty: {_clean(insight.certainty)}",
        f"Window: {_clean(window.get('start'))} to {_clean(window.get('end'))}",
        "Metrics:",
        *_metric_lines(evidence.get("metrics")),
        f"Suggested fix from the catalogue: {_clean(insight.suggested_fix)}",
        f"How to verify the fix: {_clean(insight.verification)}",
    ]
    used = list(excerpts)[:MAX_EXCERPTS]
    if used:
        lines.append(f"Trace excerpts ({len(used)}):")
        lines.extend(render_excerpt(excerpt) for excerpt in used)
    else:
        lines.append("Trace excerpts: none available.")
    return "\n".join(lines)


def render_excerpt(excerpt: TraceExcerpt) -> str:
    """One trace excerpt, cut to `MAX_EXCERPT_CHARS`.

    Every field is redacted whole before anything is cut, so a cut never leaves part of a secret
    too short for its pattern to match.
    """
    lines = [
        f"--- trace {_clean(excerpt.trace_id)}",
        f"model: {_clean(excerpt.model)}",
        f"status: {_clean(excerpt.status)}",
        f"error class: {_clean(excerpt.error_class)}",
        f"status message: {_clean(excerpt.status_message)[:MAX_STATUS_MESSAGE_CHARS]}",
        f"finish reason: {_clean(excerpt.finish_reason)}",
        f"input tokens: {_clean(excerpt.input_tokens)}",
        f"output tokens: {_clean(excerpt.output_tokens)}",
        f"input: {_payload(excerpt.input)}",
        f"output: {_payload(excerpt.output)}",
    ]
    return "\n".join(lines)[:MAX_EXCERPT_CHARS]


def worst_case_input_tokens(request: GatewayRequest) -> int:
    """The prompt's estimated size in tokens (see `app.pricing.tokens`)."""
    body = request.body
    parts = [str(body.get("system", ""))]
    parts.extend(str(message.get("content", "")) for message in body.get("messages", []))
    return estimate_text_tokens("".join(parts))


def answer_text(body: bytes | None) -> str | None:
    """The text of an Anthropic Messages answer; None when there is none or it is unreadable."""
    if body is None:
        return None
    try:
        message = json.loads(body)
    except ValueError:
        return None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    text = "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()
    return text or None


def _metric_lines(metrics: object) -> list[str]:
    if not isinstance(metrics, dict) or not metrics:
        return ["- none recorded"]
    return [f"- {_clean(name)}: {_clean(value)}" for name, value in metrics.items()]


def _clean(value: object) -> str:
    """A field as redacted text; `unknown` when it is not known."""
    if value is None:
        return UNKNOWN
    return redact_text(str(value))


def _payload(value: Any) -> str:
    """The first `MAX_PAYLOAD_CHARS` of a payload as compact JSON, redacted before it is cut."""
    if value is None:
        return "not captured"
    redacted = redact_json(value)
    rendered = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= MAX_PAYLOAD_CHARS:
        return rendered
    return rendered[:MAX_PAYLOAD_CHARS] + " [cut]"
