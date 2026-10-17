"""Scenario traffic for the error detectors: real 400s through the SDK, and gateway calls
under Integration Lab fault profiles (5xx, 429, truncated streams, unsupported parameters)."""

import time
from typing import Any

import anthropic

from accuracy.doctor.common import HAIKU, new_trace_id
from accuracy.doctor.context import RunContext, flush_sdk, parallel
from accuracy.doctor.spans import marked
from accuracy.doctor.traffic import (
    SHORT_PROMPT,
    UNSUPPORTED_PARAM,
    gw_chat,
    gw_stream,
    lab_opening,
    retry_after_s,
    sdk_chat,
    unsupported_parameter,
    wrapped,
)


def run_error_spike(ctx: RunContext, *, calls: int, errors: int) -> None:
    """`errors` of `calls` SDK calls send `top_p=7`, which Anthropic rejects with a 400 before
    any billing; the rest are ordinary short answers."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)

    def one(index: int) -> None:
        # top_p outside [0, 1] is a 400 invalid_request_error, never billed. (max_tokens=0 is
        # accepted by the API as an empty, length-stopped answer, so it is no error.)
        extra: dict[str, Any] = {"extra_body": {"top_p": 7}} if marked(index, errors, calls) else {}
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=SHORT_PROMPT.format(ref=index),
            max_tokens=60,
            model=model,
            session_id=f"es-{index % 7}",
            **extra,
        )

    parallel(one, range(calls))
    flush_sdk()


def run_error_spike_lab(ctx: RunContext, *, calls: int) -> None:
    """Gateway calls under a `provider_5xx` fault profile: the faulted 5xx spans are lab
    scenarios and must be excluded; the real calls succeed."""
    model = ctx.model(HAIKU)

    def one(index: int) -> None:
        client = ctx.gateway(session_id=f"es-lab-{index % 5}")
        gw_chat(
            ctx,
            client,
            trace_id=new_trace_id(),
            messages=lab_opening(index, new_trace_id()),
            model=model,
            max_tokens=60,
        )

    parallel(one, range(calls))


def run_retry_storm(ctx: RunContext, *, rounds: int, attempts: int) -> None:
    """The naive policy on a `provider_5xx` fault: `attempts` immediate resends per round, one
    trace per round, one session for the run."""
    model = ctx.model(HAIKU)
    client = ctx.gateway(session_id=f"storm-{new_trace_id()[:12]}")
    for index in range(1, rounds + 1):
        trace_id = new_trace_id()
        messages = lab_opening(index, trace_id)
        for _ in range(attempts):
            response, _ = gw_chat(ctx, client, trace_id=trace_id, messages=messages, model=model)
            if response is not None:
                break


def run_retry_fanout(ctx: RunContext, *, traces: int) -> None:
    """The same request from `traces` unrelated traces (no session), one attempt each: a
    fan-out of a fixed prompt, not a storm."""
    model = ctx.model(HAIKU)
    client = ctx.gateway()
    messages = [
        {
            "role": "user",
            "content": (
                f"[{ctx.nonce}] Classify this ticket as billing or technical: "
                "'card declined twice'."
            ),
        }
    ]
    for _ in range(traces):
        gw_chat(ctx, client, trace_id=new_trace_id(), messages=messages, model=model)


# --------------------------------------------------------------------------- retry_after_ignored


def run_retry_after(ctx: RunContext, *, rounds: int, attempts: int, honour: bool) -> None:
    """On a `rate_limited` fault: resend at once (`honour=False`, the naive client) or after
    `Retry-After` plus a pad (`honour=True`, the corrected client)."""
    model = ctx.model(HAIKU)
    client = ctx.gateway(session_id=f"ra-{new_trace_id()[:12]}")
    for index in range(1, rounds + 1):
        trace_id = new_trace_id()
        messages = lab_opening(index, trace_id)
        for attempt in range(1, attempts + 1):
            response, error = gw_chat(
                ctx, client, trace_id=trace_id, messages=messages, model=model
            )
            if response is not None or error is None or attempt == attempts:
                break
            if honour:
                time.sleep(retry_after_s(error) + 0.1)


def run_truncated_stream(ctx: RunContext, *, rounds: int, policy: str) -> None:
    """A `truncated_stream` fault on every streamed call.

    `naive`: take the cut stream as the whole answer and ask the follow-up in the same trace.
    `newtrace`: the same, but the follow-up starts a new trace (a new logical request).
    `corrected`: a stream without a stop reason is incomplete; resend the same request, give up
    after three tries, ask nothing else.
    """
    model = ctx.model(HAIKU)
    client = ctx.gateway(session_id=f"ts-{new_trace_id()[:12]}")
    for index in range(1, rounds + 1):
        trace_id = new_trace_id()
        messages = lab_opening(index, trace_id)
        try:
            if policy == "corrected":
                for _ in range(3):
                    outcome = gw_stream(
                        ctx, client, trace_id=trace_id, messages=messages, model=model
                    )
                    if outcome.stop_reason is not None:
                        break
                continue
            outcome = gw_stream(ctx, client, trace_id=trace_id, messages=messages, model=model)
        except anthropic.APIError as exc:
            ctx.note(f"round {index}: {type(exc).__name__}")
            continue
        follow_up = [
            *messages,
            {"role": "assistant", "content": outcome.text or "(no answer)"},
            {"role": "user", "content": "Thanks. Now give one concrete example."},
        ]
        next_trace = new_trace_id() if policy == "newtrace" else trace_id
        gw_chat(ctx, client, trace_id=next_trace, messages=follow_up, model=model, max_tokens=80)


# ---------------------------------------------------------------- unsupported_parameter_retried


def run_unsupported_parameter(
    ctx: RunContext, *, rounds: int, policy: str, delay_s: float = 0.0
) -> None:
    """An `unsupported_parameter` fault names `top_k`.

    `naive`: drop the named parameter and resend in the same trace (after `delay_s`).
    `corrected`: a 400 is a bug to fix; the round ends.
    """
    model = ctx.model(HAIKU)
    client = ctx.gateway(session_id=f"up-{new_trace_id()[:12]}")
    for index in range(1, rounds + 1):
        trace_id = new_trace_id()
        messages = lab_opening(index, trace_id)
        # The anthropic SDK refuses `top_k` as a keyword, so it travels in the body.
        body: dict[str, Any] = {UNSUPPORTED_PARAM: 40}
        response, error = gw_chat(
            ctx, client, trace_id=trace_id, messages=messages, model=model, extra_body=dict(body)
        )
        if response is not None or error is None or policy == "corrected":
            continue
        rejected = unsupported_parameter(error)
        if rejected and rejected in body:
            del body[rejected]
        if delay_s:
            time.sleep(delay_s)
        gw_chat(ctx, client, trace_id=trace_id, messages=messages, model=model, extra_body=body)


def run_rate_limit_lab(ctx: RunContext, *, calls: int) -> None:
    """Every gateway call gets the Lab's 429 (one attempt per trace): simulated pressure."""
    model = ctx.model(HAIKU)
    client = ctx.gateway()
    for index in range(calls):
        messages = lab_opening(index, new_trace_id())
        gw_chat(ctx, client, trace_id=new_trace_id(), messages=messages, model=model)
