"""Scenario traffic for the volume and shape detectors: slow, expensive, truncated, growing,
cacheable and looping calls, and SDK timeouts. All real calls through the Python SDK."""

import time
from collections.abc import Sequence
from typing import Any

from accuracy.doctor.common import HAIKU, new_trace_id
from accuracy.doctor.context import RunContext, flush_sdk, parallel
from accuracy.doctor.spans import marked
from accuracy.doctor.traffic import LONG_PROMPT, SHORT_PROMPT, VERY_LONG_PROMPT, sdk_chat, wrapped


def run_latency(ctx: RunContext, *, calls: int) -> None:
    """Long answers (~450 words) so every successful call takes seconds."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)

    def one(index: int) -> None:
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=LONG_PROMPT.format(ref=index),
            max_tokens=700,
            model=model,
            session_id=f"lat-{index % 9}",
        )

    parallel(one, range(calls), workers=8)
    flush_sdk()


# --------------------------------------------------------------------------- cost_spike


def run_cost_spike(ctx: RunContext, *, calls: int, prompt_tokens: int, model_name: str) -> None:
    """`calls` real calls with prompts of about `prompt_tokens` tokens each on `model_name`."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(model_name)
    cal = ctx.calibration()

    def one(index: int) -> None:
        body = cal.text(model, prompt_tokens - 40, seed=f"{ctx.nonce}-{index}")
        prompt = f"Read the following ledger notes.\n\n{body}\n\nReply with the single word OK."
        sdk_chat(
            ctx, sp, client, prompt=prompt, max_tokens=5, model=model, session_id=f"cs-{index % 4}"
        )

    parallel(one, range(calls), workers=4)
    flush_sdk()


def run_cost_normal(ctx: RunContext, *, calls: int) -> None:
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)

    def one(index: int) -> None:
        sdk_chat(ctx, sp, client, prompt=SHORT_PROMPT.format(ref=index), max_tokens=60, model=model)

    parallel(one, range(calls))
    flush_sdk()


def run_truncated_outputs(ctx: RunContext, *, calls: int, truncated: int) -> None:
    """`truncated` of `calls` calls ask a long question with `max_tokens=5`."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)

    def one(index: int) -> None:
        cut = marked(index, truncated, calls)
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=(LONG_PROMPT if cut else SHORT_PROMPT).format(ref=index),
            max_tokens=5 if cut else 1024,
            model=model,
            session_id=f"to-{index % 6}",
        )

    parallel(one, range(calls))
    flush_sdk()


# --------------------------------------------------------------------------- context_growth


GROWTH_SHAPE = (2_500, 5_000, 7_500, 10_000, 12_500, 15_000, 17_500, 21_000)
TRIMMED_SHAPE = (2_500, 5_000, 7_500, 3_000, 5_500, 3_000, 6_000, 21_000)


def run_context_growth(ctx: RunContext, *, sessions: int, shape: Sequence[int]) -> None:
    """Sessions whose per-call prompt follows `shape` (tokens per turn)."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)
    cal = ctx.calibration()

    def one(session: int) -> None:
        session_id = f"cg-{ctx.scenario_id}-{session}-{new_trace_id()[:6]}"
        for turn, tokens in enumerate(shape):
            body = cal.text(model, tokens - 60, seed=f"{ctx.nonce}-{session}-{turn}")
            prompt = f"Conversation so far:\n\n{body}\n\nReply with the single word OK."
            sdk_chat(
                ctx, sp, client, prompt=prompt, max_tokens=5, model=model, session_id=session_id
            )

    parallel(one, range(sessions), workers=3)
    flush_sdk()


# --------------------------------------------------------------------------- cache_opportunity


CACHE_PROMPT_TOKENS = 3_000


def run_cache_opportunity(ctx: RunContext, *, calls: int, cached: bool) -> None:
    """`calls` traces repeating one long prompt; with `cached`, the prompt carries a cache
    breakpoint so every call after the first reads the cache."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)
    policy = ctx.calibration().text(model, CACHE_PROMPT_TOKENS, seed=f"{ctx.nonce}-policy")
    system: Any
    if cached:
        system = [
            {
                "type": "text",
                "text": f"Company policy:\n\n{policy}",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system = f"Company policy:\n\n{policy}"
    for index in range(calls):
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=f"[{ctx.nonce}] Is a refund allowed after 30 days? Answer yes or no.",
            max_tokens=5,
            model=model,
            system=system,
            session_id=f"co-{index}",
        )
    flush_sdk()


# --------------------------------------------------------------------------- tool_loop


def run_tool_loop(ctx: RunContext, *, traces: int, repeats: int, changing: bool) -> None:
    """Agent traces: one LLM call, then `repeats` consecutive calls of one tool, with the same
    input (`changing=False`) or a changing one (`changing=True`, polling)."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)
    for index in range(traces):
        with sp.span("agent.run", kind="chain", input={"task": f"check job {index}"}):
            sp.update_trace(session_id=f"tl-{index}")
            ctx.record(sp.get_current_trace_id())
            sdk_chat(
                ctx,
                sp,
                client,
                prompt=f"Which tool should check the status of job {index}? One word.",
                max_tokens=20,
                model=model,
                name="agent.plan",
            )
            for attempt in range(repeats):
                tool_input = (
                    {"job_id": f"job-{index}", "attempt": attempt}
                    if changing
                    else {"job_id": f"job-{index}"}
                )
                with sp.span(
                    "poll_job" if changing else "search_docs", kind="tool", input=tool_input
                ):
                    time.sleep(0.03)
    flush_sdk()


def run_client_timeout(ctx: RunContext, *, timeouts: Sequence[float], successes: int) -> None:
    """One SDK call per entry of `timeouts` (seconds) on a prompt that needs longer than any of
    them, then `successes` calls with a generous timeout."""
    sp = ctx.sdk()
    model = ctx.model(HAIKU)

    def abort(item: tuple[int, float]) -> None:
        index, timeout = item
        client = wrapped(ctx, sp, timeout=timeout)
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=VERY_LONG_PROMPT.format(ref=f"t{index}"),
            max_tokens=1000,
            model=model,
            session_id=f"ct-{index % 4}",
        )

    def succeed(index: int) -> None:
        client = wrapped(ctx, sp, timeout=120.0)
        sdk_chat(
            ctx,
            sp,
            client,
            prompt=VERY_LONG_PROMPT.format(ref=f"s{index}"),
            max_tokens=1000,
            model=model,
            session_id=f"ct-ok-{index}",
        )

    parallel(abort, list(enumerate(timeouts)), workers=4)
    parallel(succeed, range(successes), workers=4)
    flush_sdk()
