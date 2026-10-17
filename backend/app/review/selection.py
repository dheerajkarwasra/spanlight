"""One project's review selection for one UTC day, in one transaction bound to the project.

The order inside the transaction is the idempotency: the `review_selections` row is claimed
first (`ON CONFLICT DO NOTHING`), so a day already selected ends `already_selected` before any
trace is read; a project whose policy is gone or disabled releases the claim again and ends
`no_policy`. Then four bounded reads (one per enabled reason, a heartbeat after each),
`select_items` under the cap, the item insert and the completion of the selection row. A crash
before the commit leaves nothing; the job's next attempt starts over. The metrics are
incremented after the commit, so a rolled-back run counts nothing.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import REVIEW_SELECTED, feature_used
from app.db.models.annotations import ReviewReason
from app.db.rls import bind_project
from app.review import queries, selection_queries
from app.review.calendar import day_window
from app.review.select import PRIORITY, Candidate, select_items
from app.review.selection_queries import CandidateRows, EnabledPolicy, Window

Heartbeat = Callable[[], Awaitable[None]]
Reader = Callable[..., Awaitable[CandidateRows]]


@dataclass(frozen=True, slots=True)
class SelectionResult:
    outcome: Literal["selected", "already_selected", "no_policy"]
    queued: dict[str, int] = field(default_factory=dict)
    candidates: dict[str, int] = field(default_factory=dict)
    skipped_duplicates: int = 0
    cap_hit: bool = False


def _readers(policy: EnabledPolicy) -> list[tuple[ReviewReason, Reader | None]]:
    """The read per reason in `PRIORITY` order; `None` for a reason the policy leaves out."""
    enabled = {
        ReviewReason.DOCTOR_FINDING: policy.include_doctor_findings,
        ReviewReason.ERROR: policy.include_errors,
        ReviewReason.COST_OUTLIER: policy.include_cost_outliers,
        ReviewReason.RANDOM: policy.random_rate > 0,
    }
    reads: dict[ReviewReason, Reader] = {
        ReviewReason.DOCTOR_FINDING: selection_queries.doctor_candidates,
        ReviewReason.ERROR: selection_queries.error_candidates,
        ReviewReason.COST_OUTLIER: selection_queries.outlier_candidates,
        ReviewReason.RANDOM: selection_queries.random_candidates,
    }
    return [(reason, reads[reason] if enabled[reason] else None) for reason in PRIORITY]


async def _read_candidates(
    db: AsyncSession, policy: EnabledPolicy, window: Window, heartbeat: Heartbeat | None
) -> tuple[list[Candidate], dict[str, int]]:
    """Every enabled reason's candidates (at most the cap each) and the pre-cap counts."""
    candidates: list[Candidate] = []
    counts: dict[str, int] = {}
    for reason, read in _readers(policy):
        counts[reason.value] = 0
        if read is not None:
            rows = await read(db, policy, window, limit=policy.daily_cap)
            candidates.extend(rows.rows)
            counts[reason.value] = rows.total
        if heartbeat is not None:
            await heartbeat()
    return candidates, counts


def _cap_hit(selected: int, candidates: list[Candidate], counts: dict[str, int], cap: int) -> bool:
    """Whether the cap left a candidate out: the cap is full and either a reason had more rows
    than its bounded read returned or the reads held more distinct traces than were queued."""
    if selected < cap:
        return False
    distinct = len({candidate.trace_id for candidate in candidates})
    return distinct > selected or any(total > cap for total in counts.values())


async def select_for_project(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    day: date,
    *,
    now: datetime,
    heartbeat: Heartbeat | None = None,
) -> SelectionResult:
    """Select `day`'s traces into the project's review queue; see the module docstring."""
    started = time.monotonic()
    window = day_window(day)
    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        if not await selection_queries.claim_selection(db, project_id, day, None, now):
            return SelectionResult("already_selected")
        stored = await queries.get_policy(db, project_id)
        if stored is None or not stored.enabled:
            await selection_queries.release_selection(db, project_id, day)
            return SelectionResult("no_policy")
        policy = EnabledPolicy.of(stored)
        candidates, counts = await _read_candidates(db, policy, window, heartbeat)
        selected = select_items(candidates, policy.daily_cap)
        inserted = await selection_queries.insert_selected_items(db, policy.queue_id, selected, now)
        queued = dict.fromkeys(counts, 0) | inserted  # the same keys as `candidates`
        skipped = len(selected) - sum(inserted.values())
        skipped += await selection_queries.already_queued(db, policy, window)
        result = SelectionResult(
            "selected",
            queued=queued,
            candidates=counts,
            skipped_duplicates=skipped,
            cap_hit=_cap_hit(len(selected), candidates, counts, policy.daily_cap),
        )
        await selection_queries.finish_selection(
            db,
            project_id,
            day,
            queue_id=policy.queue_id,
            candidates=counts,
            queued=queued,
            skipped_duplicates=skipped,
            cap_hit=result.cap_hit,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    for reason, count in queued.items():
        REVIEW_SELECTED.labels(reason=reason).inc(count)
    feature_used("review_selection")
    return result
