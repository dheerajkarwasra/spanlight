"""The database side of a project's replay budget: reserve before the call, settle after, reap
the dead (spec §6.6, ADR 0015).

The steps are the judge's (`app.evals.reservation`), scoped per project per UTC month and
written to `replay_runs`. Every function runs in the caller's transaction and the caller commits:

1. `reserve_replay`, in one short transaction of its own: under an advisory lock per project,
   the month's committed spend (settled `done`/`failed` costs plus the reservations of
   `running` replays) plus the estimate must stay within the budget. The row is inserted
   `running` holding the estimate, or `failed` with `budget_exceeded` and no charge. Commit
   before calling the provider.
2. The caller calls the gateway with no transaction open.
3. `settle_replay`, in a session of its own and shielded from cancellation: the replay ends
   `done` or `failed` with what it is charged, and its reservation drops to zero.
4. `reap_abandoned_replays`, in the sampler cycle: a `running` replay whose heartbeat is older
   than `ABANDONED_AFTER` failed with `abandoned` and is charged its reservation.

The caller binds the project; only the reaper bypasses row-level security.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReplayRun, ReplayStatus
from app.evals.budget import can_spend, month_start
from app.evals.reservation import ABANDONED, ABANDONED_AFTER, BUDGET_EXCEEDED, MonthSpend

_RUNNING = ReplayStatus.RUNNING.value
_SETTLED = (ReplayStatus.DONE.value, ReplayStatus.FAILED.value)


async def lock_replay_budget(db: AsyncSession, project_id: uuid.UUID) -> None:
    """Serialize the replay budget checks of one project until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('replay:' || :project_id))"),
        {"project_id": str(project_id)},
    )


_SETTLED_SUM = func.coalesce(
    func.sum(case((ReplayRun.status.in_(_SETTLED), ReplayRun.cost_usd), else_=0)), 0
)
_RESERVED_SUM = func.coalesce(
    func.sum(case((ReplayRun.status == _RUNNING, ReplayRun.reserved_usd), else_=0)), 0
)


async def month_spend(db: AsyncSession, project_id: uuid.UUID, now: datetime) -> MonthSpend:
    """The project's replay spend since the start of `now`'s month (by `started_at`)."""
    row = (
        await db.execute(
            select(_SETTLED_SUM, _RESERVED_SUM).where(
                ReplayRun.project_id == project_id, ReplayRun.started_at >= month_start(now)
            )
        )
    ).one()
    return MonthSpend(spent=Decimal(row[0]), reserved=Decimal(row[1]))


@dataclass(frozen=True, slots=True)
class NewReplay:
    """A replay about to be reserved: what the ledger records before the call is made."""

    id: uuid.UUID
    project_id: uuid.UUID
    source_trace_id: str
    source_span_id: str
    requested_by: uuid.UUID | None
    credential_id: uuid.UUID | None
    model: str
    fidelity: str
    changed: list[str]


async def reserve_replay(
    db: AsyncSession, replay: NewReplay, *, estimate: Decimal, budget: Decimal, now: datetime
) -> bool:
    """Admit the replay under the project's budget, inserting it `running` holding `estimate`;
    False when it does not fit (the row is then `failed` with `budget_exceeded`, charged
    nothing). The caller commits before calling the provider."""
    await lock_replay_budget(db, replay.project_id)
    spend = await month_spend(db, replay.project_id, now)
    admitted = can_spend(spend.committed, estimate, budget)
    outcome = (
        {"status": _RUNNING, "reserved_usd": estimate, "heartbeat_at": now}
        if admitted
        else {
            "status": ReplayStatus.FAILED.value,
            "error": BUDGET_EXCEEDED,
            "reserved_usd": Decimal(0),
            "cost_usd": Decimal(0),
            "finished_at": now,
        }
    )
    await db.execute(
        insert(ReplayRun).values(
            id=replay.id,
            project_id=replay.project_id,
            source_trace_id=replay.source_trace_id,
            source_span_id=replay.source_span_id,
            requested_by=replay.requested_by,
            credential_id=replay.credential_id,
            model=replay.model,
            fidelity=replay.fidelity,
            changed=replay.changed,
            started_at=now,
            **outcome,
        )
    )
    return admitted


async def settle_replay(
    db: AsyncSession,
    replay_id: uuid.UUID,
    *,
    succeeded: bool,
    cost_usd: Decimal,
    error: str | None,
    result_trace_id: str | None,
    result_span_id: str | None,
    now: datetime,
) -> bool:
    """End a running replay with what it is charged and release its reservation. False when it
    is no longer running (the reaper failed it first, charging its reservation)."""
    result = await db.execute(
        update(ReplayRun)
        .where(ReplayRun.id == replay_id, ReplayRun.status == _RUNNING)
        .values(
            status=(ReplayStatus.DONE if succeeded else ReplayStatus.FAILED).value,
            cost_usd=max(cost_usd, Decimal(0)),
            reserved_usd=Decimal(0),
            error=None if succeeded else error,
            result_trace_id=result_trace_id,
            result_span_id=result_span_id,
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


async def reap_abandoned_replays(db: AsyncSession, now: datetime) -> list[Decimal]:
    """Fail, with `abandoned`, every running replay whose heartbeat is older than
    `ABANDONED_AFTER`, charging its reservation. Every project: the caller bypasses row-level
    security. Returns what each was charged."""
    rows = await db.execute(
        update(ReplayRun)
        .where(
            ReplayRun.status == _RUNNING,
            func.coalesce(ReplayRun.heartbeat_at, ReplayRun.started_at) < now - ABANDONED_AFTER,
        )
        .values(
            status=ReplayStatus.FAILED.value,
            error=ABANDONED,
            cost_usd=ReplayRun.reserved_usd,
            reserved_usd=Decimal(0),
            finished_at=now,
        )
        .returning(ReplayRun.cost_usd)
        .execution_options(synchronize_session=False)
    )
    return [Decimal(row[0]) for row in rows]


async def list_replays(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    trace_id: str | None,
    span_id: str | None,
    after: uuid.UUID | None,
    limit: int,
) -> list[ReplayRun]:
    """The project's replays, newest first (ids are time-ordered), up to `limit + 1` rows so the
    caller sees whether another page follows. `trace_id` and `span_id` narrow to a source."""
    query = (
        select(ReplayRun)
        .where(ReplayRun.project_id == project_id)
        .order_by(ReplayRun.id.desc())
        .limit(limit + 1)
    )
    if trace_id is not None:
        query = query.where(ReplayRun.source_trace_id == trace_id)
    if span_id is not None:
        query = query.where(ReplayRun.source_span_id == span_id)
    if after is not None:
        query = query.where(ReplayRun.id < after)
    return list((await db.scalars(query)).all())
