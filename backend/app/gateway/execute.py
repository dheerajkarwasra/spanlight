"""`execute`: one gateway call, from the checked request to the answer and its span.

Every gateway caller goes through here: the `/gw/v1` router with a key, and in-process callers
(the demo job, later explanations and the playground) with `key=None`. The checks run in this
order, and the first that answers ends the call:

1. the requested model against the key's `allowed_models`, before any alias is applied
   (`MODEL_NOT_ALLOWED`), then the route's targets that serve the surface
   (`NO_COMPATIBLE_TARGET`), ordered by a weighted first pick;
2. the budget guard (`BUDGET_EXCEEDED`, no upstream call);
3. the cache, for a non-streaming call with a key that has a TTL (a hit replays the answer);
4. the fault decision, for a key with a fault profile (a "before" fault answers instead of the
   provider);
5. the attempt loop (`attempts.run_attempts`): retries and fallbacks inside `timeout_ms`;
6. the cache store, for a provider's 200.

Each call becomes exactly one `llm` span (`tracing.build_span`), written in the background by
`recorder.record_soon` (or handed to the context's `span_sink`): right away for a complete
answer, and for a stream once it ends or the client goes away. A stream is returned when its
first upstream byte has arrived. The overhead and time to first token count from
`GatewayRequest.received` when the router set it, and from the start of `execute` otherwise.
"""

import asyncio
from typing import Any

import structlog

from app.db.rls import bind_project
from app.gateway.attempts import LoopResult, RoutePlan, run_attempts
from app.gateway.budget import BudgetDecision, blocked_error
from app.gateway.cache import CachedResponse
from app.gateway.call import Call
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult
from app.gateway.errors import GatewayError, internal, invalid_request, model_not_allowed
from app.gateway.fault_apply import apply_before
from app.gateway.faults import BEFORE_SCENARIOS, AppliedFault, decide_fault
from app.gateway.response_summary import summarize_response
from app.gateway.routing import compatible_targets, order_targets, resolve_model
from app.gateway.sse import StreamSummary
from app.gateway.usage_chunk import injects_usage

logger = structlog.get_logger(__name__)

MAX_ECHOED_MODEL = 256


async def execute(request: GatewayRequest, context: GatewayContext) -> GatewayResult:
    """Serve one call; never raises (an unexpected failure is a provider-shaped 500)."""
    call = Call(request, context)
    try:
        return await _run(call)
    except GatewayError as error:
        return call.fail(error)
    except asyncio.CancelledError:
        call.abandon()  # the caller went away; the call still gets its span
        raise
    except Exception:
        logger.exception("gateway.execute_failed", request_id=request.request_id)
        return call.fail(internal(request.request_id))


async def _run(call: Call) -> GatewayResult:
    request, context = call.request, call.context
    plan = plan_route(request, context)
    decision = await _check_budget(call, plan)
    if not decision.allowed:
        call.budget = decision
        return call.fail(blocked_error(decision))
    cached = await _lookup_cache(call)
    if cached is not None:
        return call.from_cache(cached)
    call.fault = _decide_fault(call)
    if call.fault is not None and call.fault.scenario in BEFORE_SCENARIOS:
        error = await apply_before(
            call.fault,
            call.envelope,
            remaining_s=call.remaining_s(),
            sleep=call.waits.sleep,
            route_timeout_ms=context.route.timeout_ms,
        )
        if error is not None:
            return call.fail(error)
    call.loop = LoopResult()  # filled as attempts are made: a cancelled call still has them
    loop = await run_attempts(
        request,
        context,
        plan,
        call.fault,
        deadline=call.deadline,
        waits=call.waits,
        result=call.loop,
    )
    summary = StreamSummary()
    if loop.succeeded and loop.body is not None:
        summary = summarize_response(request.surface, loop.body)
        await _store_cache(call, plan, loop, summary)
    return call.from_loop(loop, plan, summary)


def plan_route(request: GatewayRequest, context: GatewayContext) -> RoutePlan:
    """Step 1: the model check, then the targets that serve the surface, in the order to try."""
    model = request.body.get("model")
    if not isinstance(model, str):
        raise invalid_request()
    if context.allowed_models and model not in context.allowed_models:
        raise model_not_allowed(model[:MAX_ECHOED_MODEL])
    route = context.route
    indices = compatible_targets(
        route, request.surface, context.credentials, route_name=context.route_name
    )
    weights = [route.targets[index].weight for index in indices]
    order = order_targets(indices, weights, context.rng)
    return RoutePlan(model=model, order=order, body=upstream_body(request))


def upstream_body(request: GatewayRequest) -> dict[str, Any]:
    """The body sent upstream: a streaming chat completion asks for usage unless it says itself.

    Without `stream_options.include_usage` OpenAI sends no usage on a stream, and the span would
    have no tokens or cost. No other change is made. The usage chunk this adds is kept from the
    client (`app.gateway.usage_chunk`).
    """
    body = request.body
    if injects_usage(request.surface, request.stream, body):
        return {**body, "stream_options": {"include_usage": True}}
    return body


async def _check_budget(call: Call, plan: RoutePlan) -> BudgetDecision:
    """Step 2, in a short transaction of its own under the project binding."""
    context = call.context
    target_models = [
        resolve_model(context.route.targets[index], plan.model) for index in plan.order
    ]
    async with context.sessions() as db, db.begin():
        await bind_project(db, context.project_id)
        return await context.budget_guard.check(
            db,
            context.project_id,
            context.key_id,
            plan.model,
            call.request.trace.user_id,
            call.now,
            target_models=target_models,
        )


async def _lookup_cache(call: Call) -> CachedResponse | None:
    """Step 3. Its own transaction, committed at once: a hit holds the row lock until then.

    A cache that cannot be read is a miss, never a failed call.
    """
    if call.cache != "miss":
        return None
    context = call.context
    try:
        async with context.sessions() as db, db.begin():
            await bind_project(db, context.project_id)
            return await context.cache.lookup(db, context.project_id, call.cache_key, call.now)
    except Exception:
        logger.warning("gateway.cache_lookup_failed", exc_info=True)
        return None


def _decide_fault(call: Call) -> AppliedFault | None:
    """Step 4: only calls made with a key can get a fault."""
    context = call.context
    if context.key is None:
        return None
    return decide_fault(
        context.fault_profile,
        context.environment,
        stream=call.request.stream,
        rng=context.rng,
        now=call.now,
    )


async def _store_cache(
    call: Call, plan: RoutePlan, loop: LoopResult, summary: StreamSummary
) -> None:
    """Step 6: keep a provider's 200, unless a fault altered it or its usage is unknown."""
    context, ttl = call.context, call.context.cache_ttl_seconds
    if call.cache != "miss" or ttl is None or call.fault is not None or loop.body is None:
        return
    if summary.usage is None or loop.status is None:
        return  # a hit must report what the answer cost; without usage there is nothing to say
    try:
        async with context.sessions() as db, db.begin():
            await bind_project(db, context.project_id)
            await context.cache.store(
                db,
                context.project_id,
                call.cache_key,
                status_code=loop.status,
                model=summary.model or plan.model,
                body=loop.body,
                content_type=loop.headers.get("content-type", "application/json"),
                usage=summary.usage,
                ttl_seconds=ttl,
                now=call.now,
            )
    except Exception:
        logger.warning("gateway.cache_store_failed", exc_info=True)
