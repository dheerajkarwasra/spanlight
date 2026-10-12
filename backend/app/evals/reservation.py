"""The database side of a judge's budget: reserve before the call, settle after, reap the dead.

The rules are pure in `app.evals.budget`; these functions read and write `evaluator_runs`
(ADR 0015, spec §8.4). Every function runs in the caller's transaction and the caller commits:

1. `reserve_run`, in one short transaction of its own: under an advisory lock per evaluator, the
   month's committed spend (settled `done`/`failed` costs plus the reservations of `running`
   runs) plus the estimate must stay within the budget. The run becomes `running` holding the
   estimate, or `failed` with `budget_exceeded` and no charge. Commit before calling the
   provider, so no connection is held while it answers and the next reservation sees this one.
2. The caller calls the gateway with no transaction open.
3. `settle_run`, in a session of its own and shielded from cancellation by the caller: the run
   ends `done` or `failed` with what it is charged, and its reservation drops to zero.
4. `reap_abandoned_runs`, at the start of every sampling cycle: a `running` run whose heartbeat is
   older than `ABANDONED_AFTER` failed with `abandoned` and is charged its reservation, since a
   crashed worker may have spent it; a queued online run whose job died is failed too, uncharged.

Spend can exceed the budget by at most the slack of one estimate, and two concurrent runs cannot
both pass the edge. Runs are reached by id; the caller binds the project or bypasses row-level
security (the worker).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Text as SqlText
from sqlalchemy import and_, case, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evaluator, EvaluatorRun, EvaluatorRunSource, EvaluatorRunStatus
from app.evals.budget import can_spend, month_start
from app.jobs.queue import job_may_still_run

ABANDONED_AFTER = timedelta(minutes=10)
BUDGET_EXCEEDED = "budget_exceeded"
ABANDONED = "abandoned"
# The job that evaluates one online run; its dedupe key is `run_evaluator:<run_id>`.
RUN_JOB = "run_evaluator"
ONLINE = EvaluatorRunSource.ONLINE.value

_RUNNING = EvaluatorRunStatus.RUNNING.value
_SETTLED = (EvaluatorRunStatus.DONE.value, EvaluatorRunStatus.FAILED.value)


@dataclass(frozen=True, slots=True)
class MonthSpend:
    """An evaluator's month so far: settled costs and the reservations of running calls."""

    spent: Decimal
    reserved: Decimal

    @property
    def committed(self) -> Decimal:
        return self.spent + self.reserved


async def lock_evaluator_budget(db: AsyncSession, evaluator_id: uuid.UUID) -> None:
    """Serialize the budget checks of one evaluator until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('evaluator:' || :evaluator_id))"),
        {"evaluator_id": str(evaluator_id)},
    )


_SETTLED_SUM = func.coalesce(
    func.sum(case((EvaluatorRun.status.in_(_SETTLED), EvaluatorRun.cost_usd), else_=0)), 0
)
_RESERVED_SUM = func.coalesce(
    func.sum(case((EvaluatorRun.status == _RUNNING, EvaluatorRun.reserved_usd), else_=0)), 0
)


async def month_spend(db: AsyncSession, evaluator_id: uuid.UUID, now: datetime) -> MonthSpend:
    """The evaluator's spend since the start of `now`'s month (by `started_at`)."""
    row = (
        await db.execute(
            select(_SETTLED_SUM, _RESERVED_SUM).where(
                EvaluatorRun.evaluator_id == evaluator_id,
                EvaluatorRun.started_at >= month_start(now),
            )
        )
    ).one()
    return MonthSpend(spent=Decimal(row[0]), reserved=Decimal(row[1]))


async def month_spends(
    db: AsyncSession, evaluator_ids: Sequence[uuid.UUID], now: datetime
) -> dict[uuid.UUID, MonthSpend]:
    """`month_spend` of several evaluators in one grouped query. An evaluator without runs this
    month is absent (its spend is zero)."""
    if not evaluator_ids:
        return {}
    rows = await db.execute(
        select(EvaluatorRun.evaluator_id, _SETTLED_SUM, _RESERVED_SUM)
        .where(
            EvaluatorRun.evaluator_id.in_(set(evaluator_ids)),
            EvaluatorRun.started_at >= month_start(now),
        )
        .group_by(EvaluatorRun.evaluator_id)
    )
    return {
        evaluator_id: MonthSpend(spent=Decimal(spent), reserved=Decimal(reserved))
        for evaluator_id, spent, reserved in rows
    }


async def reserve_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    estimate: Decimal,
    budget: Decimal,
    now: datetime,
) -> bool:
    """Admit the queued run `run_id` under the budget, holding `estimate`; False when it does not
    fit (the run is then `failed` with `budget_exceeded`) or the run is no longer queued. The
    caller commits before calling the provider."""
    evaluator_id = await db.scalar(
        select(EvaluatorRun.evaluator_id).where(
            EvaluatorRun.id == run_id, EvaluatorRun.status == EvaluatorRunStatus.QUEUED.value
        )
    )
    if evaluator_id is None:
        return False
    await lock_evaluator_budget(db, evaluator_id)
    spend = await month_spend(db, evaluator_id, now)
    admitted = can_spend(spend.committed, estimate, budget)
    values = (
        {"status": _RUNNING, "reserved_usd": estimate, "heartbeat_at": now, "started_at": now}
        if admitted
        else {
            "status": EvaluatorRunStatus.FAILED.value,
            "error": BUDGET_EXCEEDED,
            "cost_usd": Decimal(0),
            "reserved_usd": Decimal(0),
            "finished_at": now,
        }
    )
    result = await db.execute(
        update(EvaluatorRun)
        .where(EvaluatorRun.id == run_id, EvaluatorRun.status == EvaluatorRunStatus.QUEUED.value)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    # Two callers may both have read the run as queued before the lock; only the one whose update
    # took the row holds a reservation.
    return admitted and getattr(result, "rowcount", 0) == 1


async def settle_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    succeeded: bool,
    cost_usd: Decimal,
    error: str | None,
    now: datetime,
) -> bool:
    """End a running run with what it is charged and release its reservation. `cost_usd` comes
    from the price book (`app.evals.budget.failed_call_charge` for failures). False when the run
    is no longer running (the reaper failed it first, charging its reservation)."""
    result = await db.execute(
        update(EvaluatorRun)
        .where(EvaluatorRun.id == run_id, EvaluatorRun.status == _RUNNING)
        .values(
            status=(EvaluatorRunStatus.DONE if succeeded else EvaluatorRunStatus.FAILED).value,
            cost_usd=max(cost_usd, Decimal(0)),
            reserved_usd=Decimal(0),
            error=None if succeeded else error,
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


@dataclass(frozen=True, slots=True)
class ReapedRun:
    """A run the reaper failed: its evaluator's kind and what it was charged."""

    kind: str
    cost_usd: Decimal


async def reap_abandoned_runs(db: AsyncSession, now: datetime) -> list[ReapedRun]:
    """Fail, with `abandoned`, every running run whose heartbeat is older than `ABANDONED_AFTER`
    (charging its reservation), and every queued online run whose `run_evaluator` job is no
    longer queued or running (its single attempt died with a worker, so it would otherwise show
    as queued for ever). Test and experiment runs have no job and are never reaped while queued.
    Every project: the caller bypasses row-level security. Returns the runs."""
    kind = select(Evaluator.kind).where(Evaluator.id == EvaluatorRun.evaluator_id).scalar_subquery()
    stale = and_(
        EvaluatorRun.status == _RUNNING,
        func.coalesce(EvaluatorRun.heartbeat_at, EvaluatorRun.started_at) < now - ABANDONED_AFTER,
    )
    job_alive = job_may_still_run(func.concat(RUN_JOB + ":", cast(EvaluatorRun.id, SqlText)))
    orphaned = and_(
        EvaluatorRun.status == EvaluatorRunStatus.QUEUED.value,
        EvaluatorRun.source == ONLINE,
        ~job_alive,
    )
    rows = await db.execute(
        update(EvaluatorRun)
        .where(or_(stale, orphaned))
        .values(
            status=EvaluatorRunStatus.FAILED.value,
            error=ABANDONED,
            cost_usd=EvaluatorRun.reserved_usd,
            reserved_usd=Decimal(0),
            finished_at=now,
        )
        .returning(kind, EvaluatorRun.cost_usd)
        .execution_options(synchronize_session=False)
    )
    return [ReapedRun(kind=row[0], cost_usd=Decimal(row[1])) for row in rows]
