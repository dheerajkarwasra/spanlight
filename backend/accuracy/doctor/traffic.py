"""Shared traffic pieces of the scenarios: prompts, windows, and one-call helpers.

The naive and corrected client behaviours of `examples/naive_client.py` and
`examples/corrected_client.py` are ported to the Messages API here, since an Anthropic
credential can only be reached through the gateway's `/gw/v1/messages` surface.
"""

import re
from datetime import timedelta
from typing import Any

import anthropic

from accuracy.doctor.clients import StreamOutcome, est_tokens, guarded_create, guarded_stream
from accuracy.doctor.common import TRACE_ID_HEADER
from accuracy.doctor.context import RunContext

MINUTES = timedelta(minutes=1)
HOURS = timedelta(hours=1)

SHORT_PROMPT = "In one sentence, what is a retry budget? (ref {ref})"
LONG_PROMPT = (
    "Write about 450 words explaining how a message queue handles retries, backoff and "
    "dead-letter queues, with one worked example. (ref {ref})"
)
VERY_LONG_PROMPT = (
    "Write about 700 words on how a payment ledger reconciles partial refunds, chargebacks "
    "and currency rounding, with three worked examples. (ref {ref})"
)
LAB_PROMPT = (
    "In two or three sentences, explain what a retry budget is. (lab round {index}, ref {ref})"
)
UNSUPPORTED_PARAM = "top_k"
RATE_LIMIT_RETRY_AFTER_S = 2
_QUOTED_PARAM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*:")


def sdk_chat(
    ctx: RunContext,
    sp: Any,
    client: Any,
    *,
    prompt: str,
    max_tokens: int,
    model: str,
    session_id: str | None = None,
    system: Any = None,
    name: str = "assistant.reply",
    **extra: Any,
) -> Any:
    """One SDK-traced call inside its own trace; returns the message or None on a provider error."""
    kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": prompt}], **extra}
    if system is not None:
        kwargs["system"] = system
    system_text = system if isinstance(system, str) else ""
    with sp.span(name, kind="chain", input={"prompt": prompt[:160]}):
        if session_id:
            sp.update_trace(session_id=session_id)
        ctx.record(sp.get_current_trace_id())
        try:
            response = guarded_create(
                client,
                scenario=ctx.scenario_id,
                model=model,
                path="sdk",
                est_prompt_tokens=est_tokens(prompt, system_text),
                max_tokens=max_tokens,
                **kwargs,
            )
        except anthropic.APIError as exc:
            ctx.count(failed=True)
            ctx.note(f"{name}: {type(exc).__name__}")
            return None
        ctx.count()
        return response


def gw_chat(
    ctx: RunContext,
    client: Any,
    *,
    trace_id: str,
    messages: list[dict[str, Any]],
    model: str,
    max_tokens: int = 64,
    **extra: Any,
) -> tuple[Any, BaseException | None]:
    """One gateway call joined to `trace_id`; `(message, None)` or `(None, error)`."""
    ctx.record(trace_id)
    text = " ".join(str(m.get("content", "")) for m in messages)
    try:
        response = guarded_create(
            client,
            scenario=ctx.scenario_id,
            model=model,
            path="gateway",
            est_prompt_tokens=est_tokens(text),
            max_tokens=max_tokens,
            messages=messages,
            extra_headers={TRACE_ID_HEADER: trace_id},
            **extra,
        )
    except anthropic.APIError as exc:
        ctx.count(failed=True)
        return None, exc
    ctx.count()
    return response, None


def gw_stream(
    ctx: RunContext,
    client: Any,
    *,
    trace_id: str,
    messages: list[dict[str, Any]],
    model: str,
    max_tokens: int = 80,
) -> StreamOutcome:
    """One streamed gateway call joined to `trace_id`; raises on an HTTP error."""
    ctx.record(trace_id)
    text = " ".join(str(m.get("content", "")) for m in messages)
    outcome = guarded_stream(
        client,
        scenario=ctx.scenario_id,
        model=model,
        path="gateway",
        est_prompt_tokens=est_tokens(text),
        max_tokens=max_tokens,
        messages=messages,
        extra_headers={TRACE_ID_HEADER: trace_id},
    )
    ctx.count(failed=outcome.stop_reason is None)
    return outcome


def lab_opening(index: int, trace_id: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": LAB_PROMPT.format(index=index, ref=trace_id[:8])}]


def wrapped(ctx: RunContext, sp: Any, timeout: float = 120.0) -> Any:
    return sp.wrap_anthropic(ctx.direct(timeout))


def retry_after_s(exc: BaseException) -> float:
    """The `Retry-After` the gateway sent, or the Lab profile's value when the header is absent."""
    response = getattr(exc, "response", None)
    raw = response.headers.get("retry-after", "") if response is not None else ""
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(RATE_LIMIT_RETRY_AFTER_S)


def unsupported_parameter(exc: BaseException) -> str | None:
    """The parameter an Anthropic-shaped `unsupported_parameter` 400 names, else None."""
    message = getattr(exc, "message", None) or str(exc)
    match = re.search(r"\b([A-Za-z_][\w.]*): Extra inputs", message)
    if match:
        return match.group(1)
    match = _QUOTED_PARAM.search(message)
    return match.group(1) if match else None
