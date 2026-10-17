"""The nightly review selection's reads and writes.

`enabled_policies` lists every project's enabled policy and needs the row-level security bypass
set by its caller (the nightly job, the one place that sets it). Every other function runs in a
transaction bound to the policy's project: the selection row claim and release, the four
candidate reads (bounded to `limit` rows each, ordered by the sampling hash), the item insert and
the completion of the selection row.
"""

import json
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import ReviewPolicy, ReviewSelection
from app.db.models.annotations import ReviewReason
from app.evals.sampler_queries import MAX_TRACE_DURATION
from app.evals.sampling import EXCLUDED_ENVIRONMENTS, JUDGE_TAG, sample_threshold
from app.review import reasons, selection_sql
from app.review.outliers import OUTLIER_MIN_SAMPLE
from app.review.select import Candidate, Selected

# Traces Spanlight makes itself; belt and braces over the excluded environments.
EXCLUDED_TAGS = (JUDGE_TAG, "spanlight:playground", "spanlight:replay")
EXCLUDED_TAG_PREFIX = "spanlight:experiment:%"
# The outlier window: the 7 UTC days ending with the selected day.
OUTLIER_WINDOW_DAYS = 7

Window = tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class EnabledPolicy:
    id: uuid.UUID
    project_id: uuid.UUID
    queue_id: uuid.UUID
    random_rate: Decimal
    include_errors: bool
    include_cost_outliers: bool
    include_doctor_findings: bool
    daily_cap: int
    environments: tuple[str, ...]

    @classmethod
    def of(cls, policy: ReviewPolicy) -> "EnabledPolicy":
        return cls(
            id=policy.id,
            project_id=policy.project_id,
            queue_id=policy.queue_id,
            random_rate=policy.random_rate,
            include_errors=policy.include_errors,
            include_cost_outliers=policy.include_cost_outliers,
            include_doctor_findings=policy.include_doctor_findings,
            daily_cap=policy.daily_cap,
            environments=tuple(policy.environments),
        )


@dataclass(frozen=True, slots=True)
class CandidateRows:
    """One reason's candidates, at most the read's limit, and how many there were before it."""

    rows: list[Candidate]
    total: int


async def enabled_policies(db: AsyncSession) -> list[EnabledPolicy]:
    """Every enabled policy in every project; the caller has set the bypass."""
    policies = await db.scalars(
        select(ReviewPolicy).where(ReviewPolicy.enabled.is_(True)).order_by(ReviewPolicy.project_id)
    )
    return [EnabledPolicy.of(policy) for policy in policies]


async def claim_selection(
    db: AsyncSession, project_id: uuid.UUID, day: date, queue_id: uuid.UUID | None, now: datetime
) -> bool:
    """Insert the project-day's selection row; False when the day is already selected."""
    statement = (
        insert(ReviewSelection)
        .values(project_id=project_id, day=day, queue_id=queue_id, ran_at=now)
        .on_conflict_do_nothing(index_elements=[ReviewSelection.project_id, ReviewSelection.day])
        .returning(ReviewSelection.day)
    )
    return (await db.execute(statement)).scalar_one_or_none() is not None


async def release_selection(db: AsyncSession, project_id: uuid.UUID, day: date) -> None:
    """Take a claim back: the project turned out to have no enabled policy."""
    await db.execute(
        delete(ReviewSelection).where(
            ReviewSelection.project_id == project_id, ReviewSelection.day == day
        )
    )


def _params(policy: EnabledPolicy, window: Window, limit: int) -> dict[str, Any]:
    day_start, day_end = window
    return {
        "project_id": str(policy.project_id),
        "queue_id": str(policy.queue_id),
        "scan_from": day_start - MAX_TRACE_DURATION,
        "day_start": day_start,
        "day_end": day_end,
        "excluded": sorted(EXCLUDED_ENVIRONMENTS),
        "excluded_tags": list(EXCLUDED_TAGS),
        "excluded_tag_prefix": EXCLUDED_TAG_PREFIX,
        "environments": list(policy.environments),
        "hash_prefix": f"{policy.id}:",
        "limit": limit,
    }


def _rows(
    rows: Sequence[Row[Any]], reason: ReviewReason, detail: Callable[[Row[Any]], dict[str, Any]]
) -> CandidateRows:
    candidates = [
        Candidate(row.trace_id, reason, int(row.sample_point), detail(row)) for row in rows
    ]
    return CandidateRows(candidates, int(rows[0].total) if rows else 0)


async def doctor_candidates(
    db: AsyncSession, policy: EnabledPolicy, window: Window, *, limit: int
) -> CandidateRows:
    """Traces of the day named by an open insight's `evidence.trace_ids`, with that insight."""
    rows = (await db.execute(selection_sql.DOCTOR, _params(policy, window, limit))).all()
    return _rows(
        rows,
        ReviewReason.DOCTOR_FINDING,
        lambda row: reasons.doctor_finding_detail(row.insight_id, row.kind),
    )


async def error_candidates(
    db: AsyncSession, policy: EnabledPolicy, window: Window, *, limit: int
) -> CandidateRows:
    """Traces of the day with `error_count > 0`, with their first failed span."""
    rows = (await db.execute(selection_sql.ERROR, _params(policy, window, limit))).all()
    return _rows(
        rows,
        ReviewReason.ERROR,
        lambda row: reasons.error_detail(row.error_count, row.first_error_span_id, row.error_class),
    )


async def outlier_candidates(
    db: AsyncSession, policy: EnabledPolicy, window: Window, *, limit: int
) -> CandidateRows:
    """Traces of the day whose cost is above the p99 of their name over the last 7 days."""
    day_start, _ = window
    week_start = day_start - timedelta(days=OUTLIER_WINDOW_DAYS - 1)
    params = {
        **_params(policy, window, limit),
        "week_start": week_start,
        "week_scan_from": week_start - MAX_TRACE_DURATION,
        "min_sample": OUTLIER_MIN_SAMPLE,
    }
    rows = (await db.execute(selection_sql.OUTLIER, params)).all()
    return _rows(
        rows,
        ReviewReason.COST_OUTLIER,
        lambda row: reasons.cost_outlier_detail(
            row.cost_usd, row.p99_usd, row.trace_name, int(row.sample)
        ),
    )


async def random_candidates(
    db: AsyncSession, policy: EnabledPolicy, window: Window, *, limit: int
) -> CandidateRows:
    """Traces of the day whose sampling hash falls under the policy's random rate."""
    params = {**_params(policy, window, limit), "threshold": sample_threshold(policy.random_rate)}
    rows = (await db.execute(selection_sql.RANDOM, params)).all()
    detail = reasons.random_detail(policy.random_rate)
    return _rows(rows, ReviewReason.RANDOM, lambda _: dict(detail))


async def already_queued(db: AsyncSession, policy: EnabledPolicy, window: Window) -> int:
    """How many traces of the day the queue already held (a manual add, a late span that moved
    a trace into this day); the candidate reads left them out."""
    params = _params(policy, window, 0)
    del params["limit"]
    return int((await db.execute(selection_sql.ALREADY_QUEUED, params)).scalar_one())


async def insert_selected_items(
    db: AsyncSession, queue_id: uuid.UUID, items: Sequence[Selected], now: datetime
) -> dict[str, int]:
    """Queue the items as trace-level entries; one the queue got meanwhile is skipped. Returns
    how many were inserted per reason."""
    if not items:
        return {}
    rows = await db.execute(
        selection_sql.INSERT_ITEMS,
        {
            "queue_id": str(queue_id),
            "now": now,
            "ids": [new_id() for _ in items],
            "trace_ids": [item.trace_id for item in items],
            "reasons": [item.reason.value for item in items],
            "details": [json.dumps(item.detail) for item in items],
        },
    )
    return dict(Counter(row.reason for row in rows))


async def finish_selection(
    db: AsyncSession,
    project_id: uuid.UUID,
    day: date,
    *,
    queue_id: uuid.UUID,
    candidates: dict[str, int],
    queued: dict[str, int],
    skipped_duplicates: int,
    cap_hit: bool,
    duration_ms: int,
) -> None:
    """Complete the claimed selection row with the queue it filled and its counts."""
    await db.execute(
        update(ReviewSelection)
        .where(ReviewSelection.project_id == project_id, ReviewSelection.day == day)
        .values(
            queue_id=queue_id,
            candidates=candidates,
            queued=queued,
            skipped_duplicates=skipped_duplicates,
            cap_hit=cap_hit,
            duration_ms=duration_ms,
        )
    )
