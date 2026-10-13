"""The support-bot flow the demo job runs: classify, look up an FAQ entry, answer.

Its two LLM calls go through the gateway (`execute`) with the trace headers of the trace the flow
started, so the gateway's `llm` spans land in it, under the root span. Each call's span is
written before the flow goes on (`DirectSpans`, not the background recorder): the job's monthly
cap counts the costs on stored spans, so a paid call whose span could not be stored, or never
produced one, ends the flow with `DemoSpendUnrecordedError` and no further provider call. The
flow collects the spans it writes itself, the root `chain` span and the `faq-lookup` retrieval
span, for the job to ingest.
"""

import dataclasses
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.gateway.context import Clock, GatewayContext, GatewayRequest, GatewayResult
from app.gateway.execute import execute
from app.gateway.recorder import DirectSpans
from app.gateway.trace_headers import TraceHeaders
from app.gateway.tracing import new_span_id, new_trace_id, status_message

DEMO_MODEL = "claude-haiku-4-5"

CATEGORIES = ("billing", "shipping", "account", "other")

FAQ: dict[str, str] = {
    "billing": (
        "Invoices are emailed on the 1st of each month. Refunds for annual plans are "
        "prorated and issued within 5 business days to the original payment method."
    ),
    "shipping": (
        "Orders ship within 2 business days. Standard delivery takes 3-5 days; tracking "
        "links are sent by email once the parcel leaves the warehouse."
    ),
    "account": (
        "Passwords can be reset from the sign-in page. Two-factor authentication can be "
        "enabled under Settings → Security. Accounts can be deleted from Settings → Account."
    ),
    "other": "For anything else, our support team replies within one business day.",
}

QUESTIONS = (
    "I was charged twice this month, can I get one of the payments back?",
    "When will my order arrive? I ordered it on Monday.",
    "How do I turn on two-factor authentication?",
    "Do you have an office in Berlin I could visit?",
    "I cancelled my annual plan halfway through. Do I get anything back?",
    "My tracking link doesn't work yet, is that normal?",
    "I forgot my password and can't log in.",
)

CLASSIFY_SYSTEM = (
    "Classify the customer's support question into exactly one category: "
    "billing, shipping, account or other. Reply with the category word only."
)
ANSWER_SYSTEM = (
    "You are a concise, friendly support agent. Answer the customer in at most three "
    "sentences using only the FAQ entry provided. If it does not cover the question, "
    "say a human will follow up."
)


class DemoCallError(Exception):
    """A demo LLM call that did not succeed. The message is the gateway span's status line."""


class DemoBudgetBlockedError(DemoCallError):
    """A demo LLM call that a firing project `block` budget stopped before the provider."""


class DemoSpendUnrecordedError(Exception):
    """A demo LLM call's span was not stored, so its cost escapes the monthly cap."""


@dataclass
class FlowRecorder:
    """Collects the native-format spans of one support-bot trace that the job writes itself.

    The LLM spans are not here: the gateway writes them, into the same trace.
    """

    question: str
    trace_id: str = field(default_factory=new_trace_id)
    session_id: str = field(default_factory=lambda: f"demo-{secrets.token_hex(4)}")
    user_id: str = field(default_factory=lambda: f"visitor-{secrets.randbelow(50):02d}")
    spans: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=lambda: ["demo"])
    llm_calls: int = 0
    error: Exception | None = None

    def trace_fields(self) -> dict[str, Any]:
        return {
            "name": "support-bot",
            "environment": "demo",
            "release": "demo-v1",
            "user_id": self.user_id,
            "session_id": self.session_id,
            "tags": self.tags,
        }

    def trace_headers(self, parent_span_id: str) -> TraceHeaders:
        """What joins a gateway call to this trace, under `parent_span_id`."""
        return TraceHeaders(
            trace_id=self.trace_id,
            parent_span_id=parent_span_id,
            session_id=self.session_id,
            user_id=self.user_id,
            tags=list(self.tags),
        )

    def add(self, span: dict[str, Any]) -> None:
        self.spans.append({**span, "trace_id": self.trace_id, "trace": self.trace_fields()})


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def answer_text(result: GatewayResult) -> str | None:
    """The text of a successful Anthropic Messages answer; None for anything else."""
    if result.error is not None or result.status != 200 or result.body is None:
        return None
    try:
        message = json.loads(result.body)
    except ValueError:
        return None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


async def _call_claude(
    gateway: GatewayContext,
    spans: DirectSpans,
    flow: FlowRecorder,
    *,
    parent_span_id: str,
    system: str,
    user_content: str,
    max_tokens: int,
) -> str | None:
    """One Messages call through the gateway; the answer's text, or None (and `flow.error`)."""
    request = GatewayRequest(
        surface="messages",
        body={
            "model": DEMO_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
        },
        stream=False,
        trace=flow.trace_headers(parent_span_id),
    )
    flow.llm_calls += 1
    result = await execute(request, gateway)
    if not await spans.write(gateway, require_span=True):
        flow.error = DemoSpendUnrecordedError(
            f"the span of demo call {flow.llm_calls} was not stored"
        )
        return None
    text = answer_text(result)
    if text is None:
        reason = status_message(result) or f"{result.status} unreadable answer"
        flow.error = (DemoBudgetBlockedError if result.budget is not None else DemoCallError)(
            reason
        )
    return text


async def run_support_flow(gateway: GatewayContext, question: str) -> FlowRecorder:
    spans = DirectSpans()
    gateway = dataclasses.replace(gateway, span_sink=spans)
    flow = FlowRecorder(question=question)
    root_span_id = new_span_id()
    started = gateway.clock()
    answer: str | None = None

    classification = await _call_claude(
        gateway,
        spans,
        flow,
        parent_span_id=root_span_id,
        system=CLASSIFY_SYSTEM,
        user_content=question,
        max_tokens=10,
    )
    if classification is not None:
        word = classification.lower().strip(" .")
        category = word if word in CATEGORIES else "other"
        flow.tags.append(category)

        faq_entry = _faq_lookup(flow, root_span_id, category, gateway.clock)
        answer = await _call_claude(
            gateway,
            spans,
            flow,
            parent_span_id=root_span_id,
            system=ANSWER_SYSTEM,
            user_content=f"FAQ entry:\n{faq_entry}\n\nCustomer question:\n{question}",
            max_tokens=300,
        )

    flow.add(_root_span(flow, root_span_id, started, gateway.clock(), answer))
    return flow


def _faq_lookup(flow: FlowRecorder, root_span_id: str, category: str, clock: Clock) -> str:
    """The FAQ entry for `category`, recorded as the `faq-lookup` retrieval span."""
    started = clock()
    entry = FAQ[category]
    flow.add(
        {
            "span_id": new_span_id(),
            "parent_span_id": root_span_id,
            "name": "faq-lookup",
            "kind": "retrieval",
            "status": "ok",
            "start_time": _iso(started),
            "end_time": _iso(clock()),
            "input": {"category": category},
            "output": {"entry": entry},
        }
    )
    return entry


def _root_span(
    flow: FlowRecorder, root_span_id: str, started: datetime, ended: datetime, answer: str | None
) -> dict[str, Any]:
    root: dict[str, Any] = {
        "span_id": root_span_id,
        "name": "support-bot",
        "kind": "chain",
        "status": "error" if flow.error else "ok",
        "start_time": _iso(started),
        "end_time": _iso(ended),
        "input": {"question": flow.question},
        "output": {"answer": answer} if answer is not None else None,
    }
    if flow.error is not None:
        root["status_message"] = f"{type(flow.error).__name__}: {flow.error}"
    return root
