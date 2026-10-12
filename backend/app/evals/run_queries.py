"""Reads and writes of one evaluator run outside the budget reservation.

Every function runs in the caller's transaction, bound to the run's project (row-level security
reaches runs through their evaluator), and the caller commits. A judge's run moves from `queued`
to `running` through `app.evals.reservation.reserve_run`; heuristic and webhook runs cost nothing
and start through `start_run`. Both end through `app.evals.reservation.settle_run`.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import (
    Evaluator,
    EvaluatorRun,
    EvaluatorRunSource,
    EvaluatorRunStatus,
    Span,
    SpanKind,
    Trace,
)

_QUEUED = EvaluatorRunStatus.QUEUED.value
_RUNNING = EvaluatorRunStatus.RUNNING.value
_MAX_ERROR = 500  # the column's CHECK


async def load_run(db: AsyncSession, run_id: uuid.UUID) -> tuple[EvaluatorRun, Evaluator] | None:
    """The run and its evaluator, or None when either is gone (deleting an evaluator deletes its
    runs)."""
    row = (
        await db.execute(
            select(EvaluatorRun, Evaluator)
            .join(Evaluator, Evaluator.id == EvaluatorRun.evaluator_id)
            .where(EvaluatorRun.id == run_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def insert_run(
    db: AsyncSession,
    evaluator_id: uuid.UUID,
    *,
    trace_id: str,
    span_id: str | None,
    source: EvaluatorRunSource,
    now: datetime,
) -> uuid.UUID:
    """Queue a `test` or `experiment` run (online runs are inserted by the sampler)."""
    run = EvaluatorRun(
        id=new_id(),
        evaluator_id=evaluator_id,
        trace_id=trace_id,
        span_id=span_id,
        status=_QUEUED,
        source=source.value,
        started_at=now,
    )
    db.add(run)
    await db.flush()
    return run.id


async def start_run(db: AsyncSession, run_id: uuid.UUID, now: datetime, *, resume: bool) -> bool:
    """Move a queued run to `running` with no reservation; False when it is not queued. With
    `resume`, a run already `running` (an earlier attempt that died) starts again too."""
    states = (_QUEUED, _RUNNING) if resume else (_QUEUED,)
    result = await db.execute(
        update(EvaluatorRun)
        .where(EvaluatorRun.id == run_id, EvaluatorRun.status.in_(states))
        .values(status=_RUNNING, heartbeat_at=now, reserved_usd=Decimal(0))
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


async def fail_unreserved_run(
    db: AsyncSession, run_id: uuid.UUID, error: str, now: datetime
) -> bool:
    """End a run that holds no reservation as `failed` at no charge; False when it had already
    ended. Never used once a judge's reservation is held: that run settles instead."""
    result = await db.execute(
        update(EvaluatorRun)
        .where(EvaluatorRun.id == run_id, EvaluatorRun.status.in_((_QUEUED, _RUNNING)))
        .values(
            status=EvaluatorRunStatus.FAILED.value,
            error=error[:_MAX_ERROR],
            cost_usd=Decimal(0),
            reserved_usd=Decimal(0),
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


async def run_status(db: AsyncSession, run_id: uuid.UUID) -> tuple[str, str | None] | None:
    """The run's status and error."""
    row = (
        await db.execute(
            select(EvaluatorRun.status, EvaluatorRun.error).where(EvaluatorRun.id == run_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def load_target(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str | None
) -> tuple[Trace, Span | None] | None:
    """The trace and the span a run evaluates; None when retention deleted the trace."""
    trace = await db.get(Trace, (project_id, trace_id))
    if trace is None:
        return None
    if span_id is None:
        return trace, None
    return trace, await db.get(Span, (project_id, trace_id, span_id))


async def last_llm_span(db: AsyncSession, project_id: uuid.UUID, trace_id: str) -> Span | None:
    """The trace's last `llm` span by start time (ties by span id), as the sampler picks it."""
    return await db.scalar(
        select(Span)
        .where(
            Span.project_id == project_id,
            Span.trace_id == trace_id,
            Span.kind == SpanKind.LLM,
        )
        .order_by(Span.started_at.desc(), Span.span_id.desc())
        .limit(1)
    )
