"""Control traffic: clean real applications (chat, agent, gateway) where no detector should
fire."""

import time
from typing import Any

import anthropic

from accuracy.doctor.clients import est_tokens, guarded_create
from accuracy.doctor.common import HAIKU, new_trace_id
from accuracy.doctor.context import RunContext, flush_sdk, parallel
from accuracy.doctor.traffic import SHORT_PROMPT, gw_chat, gw_stream, sdk_chat, wrapped


def run_control_chat(ctx: RunContext, *, sessions: int, turns: int) -> None:
    """Ordinary chat: short conversations that carry their history."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)
    questions = (
        "What is a dead-letter queue?",
        "When would I use one?",
        "Name one risk of using it badly.",
        "How do I monitor it?",
    )

    def one(session: int) -> None:
        session_id = f"chat-{ctx.scenario_id}-{session}-{new_trace_id()[:6]}"
        history: list[dict[str, Any]] = []
        for turn in range(turns):
            history.append({"role": "user", "content": questions[turn % len(questions)]})
            with sp.span("assistant.reply", kind="chain"):
                sp.update_trace(session_id=session_id, user_id=f"user-{session}")
                ctx.record(sp.get_current_trace_id())
                try:
                    response = guarded_create(
                        client,
                        scenario=ctx.scenario_id,
                        model=model,
                        path="sdk",
                        est_prompt_tokens=est_tokens(*(str(m["content"]) for m in history)),
                        max_tokens=120,
                        messages=history,
                    )
                except anthropic.APIError as exc:
                    ctx.count(failed=True)
                    ctx.note(f"chat: {type(exc).__name__}")
                    return
                ctx.count()
            text = "".join(getattr(block, "text", "") for block in response.content)
            history.append({"role": "assistant", "content": text or "(no answer)"})

    parallel(one, range(sessions), workers=3)
    flush_sdk()


def run_control_agent(ctx: RunContext, *, traces: int) -> None:
    """Ordinary agent traces: plan, two different tool calls, answer."""
    sp = ctx.sdk()
    client = wrapped(ctx, sp)
    model = ctx.model(HAIKU)
    for index in range(traces):
        with sp.span("agent.run", kind="chain", input={"task": f"refund case {index}"}):
            sp.update_trace(session_id=f"agent-{index}", user_id=f"user-{index}")
            ctx.record(sp.get_current_trace_id())
            sdk_chat(
                ctx,
                sp,
                client,
                prompt=f"Plan two lookups to decide refund case {index}. One line.",
                max_tokens=60,
                model=model,
                name="agent.plan",
            )
            with sp.span("lookup_order", kind="tool", input={"order_id": f"ord-{index}"}):
                time.sleep(0.03)
            with sp.span("lookup_policy", kind="tool", input={"region": "eu", "case": index}):
                time.sleep(0.03)
            sdk_chat(
                ctx,
                sp,
                client,
                prompt=(
                    f"Given order ord-{index} is 12 days old and policy allows 30, decide. "
                    "One line."
                ),
                max_tokens=60,
                model=model,
                name="agent.answer",
            )
    flush_sdk()


def run_control_gateway(ctx: RunContext, *, calls: int) -> None:
    """Ordinary gateway traffic, no fault profile: sessions, trace ids, a few streams."""
    model = ctx.model(HAIKU)
    for index in range(calls):
        client = ctx.gateway(session_id=f"gw-{index % 4}")
        trace_id = new_trace_id()
        messages = [{"role": "user", "content": SHORT_PROMPT.format(ref=f"gw{index}")}]
        try:
            if index % 3 == 0:
                gw_stream(ctx, client, trace_id=trace_id, messages=messages, model=model)
            else:
                gw_chat(
                    ctx, client, trace_id=trace_id, messages=messages, model=model, max_tokens=80
                )
        except anthropic.APIError as exc:
            ctx.note(f"gateway call {index}: {type(exc).__name__}")
