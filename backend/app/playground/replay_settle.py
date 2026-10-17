"""The last step of a replay: the span written, the ledger row settled, the run audited.

Runs in a session of its own (not the request's, which may be torn down when the client goes
away) and under `shielded`, so cancelling the request does not cancel the settlement. The row
ends `done` charged the price-book cost of its usage (the estimate when the usage is unpriced),
or `failed` charged what the call may have billed (`app.evals.budget.failed_call_charge`): a
project `block` budget that stopped the call charges nothing (`gateway_budget_blocked`); a call
that raised or was cancelled charges the estimate (`replay_failed`). The replayed span's
`request_hash` is compared with the source's, and `replay.run` is audited with ids, the model,
the fidelity and the charge, never content. Answers are never logged.
"""

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import REPLAY_COST, REPLAY_RUNS
from app.db.models import AuditAction, Project, ProviderCredential, Span
from app.db.rls import bind_project
from app.evals.budget import failed_call_charge
from app.gateway.context import Clock, GatewayContext, GatewayServices
from app.gateway.recorder import DirectSpans
from app.playground.output import BUDGET_EXCEEDED as GATEWAY_BLOCK
from app.playground.output import output_text
from app.playground.replay_queries import settle_replay
from app.playground.replay_request import ReplayPlan
from app.playground.replay_schemas import ReplayOutcome, SourceView
from app.playground.schemas import PlaygroundResult
from app.pricing.cost import PriceBook
from app.services.audit import record_audit

logger = structlog.get_logger(__name__)

TRACE_NAME = "replay"
REPLAY_FAILED = "replay_failed"
GATEWAY_BUDGET_BLOCKED = "gateway_budget_blocked"


@dataclass(frozen=True)
class Prepared:
    """Everything one replay needs, read in the request's transaction before the reservation."""

    replay_id: uuid.UUID
    span: Span
    release: str | None
    credential: ProviderCredential
    plan: ReplayPlan
    prices: PriceBook
    estimate: Decimal


class ReplaySpans(DirectSpans):
    """Takes the call's span, naming its trace and marking which span it replays."""

    def __init__(self, prepared: Prepared) -> None:
        super().__init__()
        self._marks = {
            "spanlight.replay.source_trace_id": prepared.span.trace_id,
            "spanlight.replay.source_span_id": prepared.span.span_id,
            "spanlight.replay.fidelity": prepared.plan.fidelity,
            "spanlight.replay.changed": list(prepared.plan.changed),
            "spanlight.replay.id": str(prepared.replay_id),
        }

    def add(self, span: dict[str, Any], credential_ids: Any) -> None:
        trace = {**(span.get("trace") or {}), "name": TRACE_NAME}
        attributes = {**(span.get("attributes") or {}), **self._marks}
        super().add({**span, "trace": trace, "attributes": attributes}, credential_ids)


@dataclass(frozen=True)
class Settlement:
    """The settlement of one call; `unanswered` or `answered` runs it to the end."""

    services: GatewayServices
    project: Project
    actor_id: uuid.UUID
    prepared: Prepared
    spans: ReplaySpans
    context: GatewayContext
    clock: Clock
    ip: str | None

    async def unanswered(self) -> ReplayOutcome:
        """The call raised or was cancelled: the worst case stays charged."""
        await self.spans.write(self.context)
        failed = PlaygroundResult(None, None, None, None, None, None, None, REPLAY_FAILED)
        return await self._settle(failed, charge=self.prepared.estimate, stored=False)

    async def answered(self, outcome: PlaygroundResult, *, billed: bool) -> ReplayOutcome:
        """A call that returned (see the module docstring for what it is charged)."""
        # An answered call always leaves a span; none means it could not be built.
        stored = await self.spans.write(self.context, require_span=True)
        estimate = self.prepared.estimate
        if outcome.error is None:
            charge = estimate if outcome.cost_usd is None else outcome.cost_usd
        else:
            charge = failed_call_charge(
                may_have_billed=billed, usage_cost=outcome.cost_usd, estimate=estimate
            )
        return await self._settle(outcome, charge=charge, stored=stored)

    async def _settle(
        self, outcome: PlaygroundResult, *, charge: Decimal, stored: bool
    ) -> ReplayOutcome:
        if not stored:  # a trace link that cannot open is worse than none
            if outcome.trace_id is not None:
                logger.warning("replay_span_not_stored", project_id=str(self.project.id))
            outcome = replace(outcome, trace_id=None, span_id=None)
        error = _run_error(outcome)
        async with self.services.sessions() as db, db.begin():
            await bind_project(db, self.project.id)
            settled = await settle_replay(
                db,
                self.prepared.replay_id,
                succeeded=error is None,
                cost_usd=charge,
                error=error,
                result_trace_id=outcome.trace_id,
                result_span_id=outcome.span_id,
                now=self.clock(),
            )
            same_hash = await _same_request_hash(db, self.project.id, self.prepared.span, outcome)
            await self._audit(db, charge)
        if settled:
            REPLAY_RUNS.labels(outcome=_outcome_label(error)).inc()
            REPLAY_COST.inc(float(charge))
        else:  # the reaper failed it first, charging and counting its reservation
            logger.warning("replay_settled_late", replay_id=str(self.prepared.replay_id))
        return self._outcome(outcome, same_hash)

    def _outcome(self, result: PlaygroundResult, same_hash: bool | None) -> ReplayOutcome:
        prepared = self.prepared
        source = SourceView(
            prepared.span.trace_id, prepared.span.span_id, output_text(prepared.span.output)
        )
        return ReplayOutcome(
            prepared.replay_id,
            prepared.plan.fidelity,
            prepared.plan.changed,
            same_hash,
            source,
            result,
        )

    async def _audit(self, db: AsyncSession, charge: Decimal) -> None:
        prepared = self.prepared
        await record_audit(
            db,
            org_id=self.project.org_id,
            actor_user_id=self.actor_id,
            action=AuditAction.REPLAY_RUN,
            target_type="replay",
            target_id=prepared.replay_id,
            ip=self.ip,
            metadata={
                "source_trace_id": prepared.span.trace_id,
                "source_span_id": prepared.span.span_id,
                "credential_id": str(prepared.credential.id),
                "model": prepared.plan.model,
                "fidelity": prepared.plan.fidelity,
                "cost_usd": str(charge),
            },
        )


def _run_error(outcome: PlaygroundResult) -> str | None:
    """The ledger's run-level error: `gateway_budget_blocked` for a project budget block, else
    the result's own error (the provider's status message, `replay_failed`)."""
    return GATEWAY_BUDGET_BLOCKED if outcome.error == GATEWAY_BLOCK else outcome.error


def _outcome_label(error: str | None) -> str:
    """The bounded `outcome` label of `spanlight_replay_runs_total`."""
    if error is None:
        return "done"
    return GATEWAY_BUDGET_BLOCKED if error == GATEWAY_BUDGET_BLOCKED else "failed"


async def _same_request_hash(
    db: AsyncSession, project_id: uuid.UUID, source: Span, outcome: PlaygroundResult
) -> bool | None:
    """Whether the stored replayed span hashes to the same request as the source; None when no
    span was stored to compare."""
    if outcome.trace_id is None or outcome.span_id is None:
        return None
    replayed = await db.scalar(
        select(Span.request_hash).where(
            Span.project_id == project_id,
            Span.trace_id == outcome.trace_id,
            Span.span_id == outcome.span_id,
        )
    )
    return replayed is not None and replayed == source.request_hash
