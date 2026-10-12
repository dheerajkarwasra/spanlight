"""One experiment item: call the target, score the answer, store the item.

The call goes through the gateway in process on a route built in memory
(`single_credential_context`, the route's default timeout): environment `experiments`, trace name
`experiment:<name>`, the run's tag, and the prompt version on the span for a prompt target. The
span is written into the project like any other call (it is real spend there) and awaited,
shielded from cancellation, so a cancelled run still leaves the spans it paid for.

An answer is scored by each attached evaluator through `runner.evaluate_item`, under that
evaluator's own monthly budget; the runner also writes the scores on the item's trace (source
`eval`). An evaluator that fails or is over budget leaves its score out; if scoring itself breaks,
the item keeps its answer and cost and records `evaluator_error`.

The item row stores `{text, finish_reason}`, the latency, the cost from the price book (None when
unknown) and the scores as `{name: value}`. A call that was cancelled, or that broke on our side,
may still have been billed: its row is stored with the call's estimate as its cost (the rule of a
judge call, ADR 0015). Errors are cut to 500 characters and NUL is removed from stored text, so no
item can fail its insert. Payloads are never logged.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from app.core.observability import EXPERIMENT_ITEMS
from app.db.models import DatasetItem, Evaluator, Project, PromptVersion, ProviderCredential
from app.db.rls import bind_project
from app.evals import runner
from app.evals.experiment_config import AdHocTarget, PromptVersionTarget
from app.evals.experiment_item_row import json_score, storable_row
from app.evals.experiment_queries import insert_item
from app.evals.experiment_request import ItemRequest
from app.evals.runner_build import RunnerDeps
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult
from app.gateway.in_process import single_credential_context
from app.gateway.recorder import DirectSpans
from app.gateway.settle_helpers import shielded
from app.playground.output import result_of
from app.pricing.cost import PriceBook

logger = structlog.get_logger(__name__)

ENVIRONMENT = "experiments"
INTERNAL_ERROR = "internal_error"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunPlan:
    """Everything a run's items share, loaded once when the run starts."""

    run_id: uuid.UUID
    experiment_id: uuid.UUID
    trace_name: str
    project: Project
    credential: ProviderCredential
    target: PromptVersionTarget | AdHocTarget
    version: PromptVersion | None
    evaluators: tuple[Evaluator, ...]
    prices: PriceBook
    max_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class ItemDone:
    """What the run's cost cap needs of a finished item."""

    cost_usd: Decimal | None
    error: str | None


@dataclass(slots=True)
class _Call:
    """The item columns a call produced, and what scoring it needs (`trace_id` set: scorable)."""

    columns: dict[str, Any]
    trace_id: str | None = None
    span_id: str | None = None
    text: str | None = None


class _ExperimentSpans(DirectSpans):
    """Takes a call's span, naming its trace and tying it to the prompt version, if any."""

    def __init__(self, trace_name: str, version_id: uuid.UUID | None) -> None:
        super().__init__()
        self._trace_name = trace_name
        self._version_id = version_id

    def add(self, span: dict[str, Any], credential_ids: Any) -> None:
        trace = span.get("trace") or {}
        span["trace"] = {**trace, "name": self._trace_name}
        if self._version_id is not None:
            span["prompt_version_id"] = str(self._version_id)
        super().add(span, credential_ids)


async def run_item(
    deps: RunnerDeps, plan: RunPlan, item: DatasetItem, prepared: ItemRequest, *, at: datetime
) -> ItemDone:
    """Run one item and store its row. Raises only when cancelled (its row is stored first)."""
    values: dict[str, Any] = {"run_id": plan.run_id, "dataset_item_id": item.id}
    if prepared.request is None:
        values["error"] = prepared.error
    else:
        call = await _call(deps, plan, item, prepared, at)
        values.update(call.columns)
        if call.trace_id is not None:
            try:
                values.update(await _scored(deps, plan, item, call))
            except asyncio.CancelledError:
                # The answer was paid for: its row is stored without scores before re-raising.
                await shielded(_store(deps, plan, values))
                raise
    # Shielded: a cancellation now still lands the row, so the run's totals count the call.
    await shielded(_store(deps, plan, values))
    return ItemDone(cost_usd=values.get("cost_usd"), error=values.get("error"))


async def _call(
    deps: RunnerDeps, plan: RunPlan, item: DatasetItem, prepared: ItemRequest, at: datetime
) -> _Call:
    """Send the item's request. A failure on our side is an `internal_error` item, charged its
    estimate; a cancellation stores the item (`cancelled`, charged its estimate) and re-raises."""
    request = prepared.request
    if deps.services is None or request is None:
        raise RuntimeError("experiment items need the gateway services and a request")
    spans = _ExperimentSpans(plan.trace_name, plan.version.id if plan.version else None)
    context = single_credential_context(
        deps.services,
        plan.project,
        plan.credential,
        environment=ENVIRONMENT,
        route_name=plan.trace_name,
        span_sink=spans,
    )
    started = time.monotonic()
    try:
        result = await deps.execute(request, context)
    except asyncio.CancelledError:
        unanswered = _unanswered(CANCELLED, started, prepared.estimate)
        await shielded(_store_after(deps, plan, item, spans, context, unanswered))
        raise
    except Exception as failure:  # noqa: BLE001 - one broken item must not stop the run
        _log_failure("experiment_item_failed", plan, item, failure)
        await shielded(spans.write(context))
        return _Call(_unanswered(INTERNAL_ERROR, started, prepared.estimate))
    latency_ms = (time.monotonic() - started) * 1000
    try:
        answered = _answered(plan, request, result, latency_ms, at)
    except Exception as failure:  # noqa: BLE001 - an odd answer must not stop the run
        _log_failure("experiment_item_failed", plan, item, failure)
        await shielded(spans.write(context))
        return _Call(_unanswered(INTERNAL_ERROR, started, prepared.estimate))
    return await _with_span(deps, plan, item, answered, spans, context)


async def _with_span(
    deps: RunnerDeps,
    plan: RunPlan,
    item: DatasetItem,
    answered: _Call,
    spans: DirectSpans,
    context: GatewayContext,
) -> _Call:
    """Write the answered call's span. The call points at its trace only when the span was
    stored; a cancellation meanwhile stores the item row (unlinked) and re-raises."""
    try:
        stored = await shielded(spans.write(context))
    except asyncio.CancelledError:
        row = {"run_id": plan.run_id, "dataset_item_id": item.id, **_unlinked(answered).columns}
        await shielded(_store(deps, plan, row))
        raise
    return answered if stored else _unlinked(answered)


def _unlinked(call: _Call) -> _Call:
    """The call without its trace: nothing may point at a span that was not stored."""
    return _Call({**call.columns, "trace_id": None}, text=call.text)


def _answered(
    plan: RunPlan,
    request: GatewayRequest,
    result: GatewayResult,
    latency_ms: float,
    at: datetime,
) -> _Call:
    outcome = result_of(
        result,
        surface=request.surface,
        provider=plan.credential.provider.value,
        model=plan.target.model,
        prices=plan.prices,
        latency_ms=latency_ms,
        at=at,
    )
    columns: dict[str, Any] = {
        "trace_id": outcome.trace_id,
        "latency_ms": latency_ms,
        "cost_usd": outcome.cost_usd,
        "error": outcome.error,
    }
    if outcome.error is not None:
        return _Call(columns)
    columns["output"] = {"text": outcome.output, "finish_reason": result.finish_reason}
    return _Call(columns, trace_id=outcome.trace_id, span_id=outcome.span_id, text=outcome.output)


def _unanswered(error: str, started: float, estimate: Decimal | None) -> dict[str, Any]:
    return {
        "latency_ms": (time.monotonic() - started) * 1000,
        "cost_usd": estimate,
        "error": error,
    }


async def _store_after(
    deps: RunnerDeps,
    plan: RunPlan,
    item: DatasetItem,
    spans: DirectSpans,
    context: GatewayContext,
    columns: dict[str, Any],
) -> None:
    """Write the call's spans, then the item row of a call that did not answer."""
    await spans.write(context)
    await _store(deps, plan, {"run_id": plan.run_id, "dataset_item_id": item.id, **columns})


async def _scored(
    deps: RunnerDeps, plan: RunPlan, item: DatasetItem, call: _Call
) -> dict[str, Any]:
    """`{"scores": …}`, or `{"evaluator_error": …}` when scoring broke (the answer stays)."""
    try:
        return {"scores": await _scores(deps, plan, item, call)}
    except Exception as failure:  # noqa: BLE001 - a scoring defect must not lose a paid answer
        _log_failure("experiment_item_scoring_failed", plan, item, failure)
        return {"evaluator_error": INTERNAL_ERROR}


async def _scores(
    deps: RunnerDeps, plan: RunPlan, item: DatasetItem, call: _Call
) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    metadata = {
        "experiment_id": str(plan.experiment_id),
        "experiment_run_id": str(plan.run_id),
        "dataset_item_id": str(item.id),
    }
    for evaluator in plan.evaluators:
        outcome = await runner.evaluate_item(
            deps,
            plan.project,
            evaluator,
            trace_id=call.trace_id or "",
            span_id=call.span_id,
            input=item.input,
            output=call.text,
            expected_output=item.expected_output,
            metadata=metadata,
        )
        if outcome.status != "done":
            continue
        for score in outcome.scores:
            scores[score.name] = json_score(score.value, score.string_value)
    return scores


async def _store(deps: RunnerDeps, plan: RunPlan, values: dict[str, Any]) -> None:
    values = storable_row(values)
    async with deps.sessions() as db, db.begin():
        await bind_project(db, plan.project.id)
        await insert_item(db, values)
    EXPERIMENT_ITEMS.labels(outcome="error" if values.get("error") else "done").inc()


def _log_failure(event: str, plan: RunPlan, item: DatasetItem, failure: Exception) -> None:
    logger.error(
        event,
        run_id=str(plan.run_id),
        dataset_item_id=str(item.id),
        error_type=type(failure).__name__,
    )
