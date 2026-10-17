"""Which candidates fill the review queue under the daily cap. Pure.

Candidates arrive one per (trace, reason). A trace that matches several reasons becomes one item
under its highest-priority reason, with the others listed in `detail["also"]`. The last
`ceil(cap / 4)` slots are held for random picks, so a day full of errors still yields random reads;
held slots the random reason cannot fill go back to the higher priorities. Inside a reason the
order is by `sample_point` (the hash), never by time, so the selection is spread over the day and
identical on every re-run.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.db.models.annotations import ReviewReason

PRIORITY: tuple[ReviewReason, ...] = (
    ReviewReason.DOCTOR_FINDING,
    ReviewReason.ERROR,
    ReviewReason.COST_OUTLIER,
    ReviewReason.RANDOM,
)
_RANK = {reason: rank for rank, reason in enumerate(PRIORITY)}
_NON_RANDOM = PRIORITY[:-1]


@dataclass(frozen=True, slots=True)
class Candidate:
    """A trace matched by one reason; `detail` is that reason's `reason_detail` without `also`."""

    trace_id: str
    reason: ReviewReason
    sample_point: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Selected:
    """One queue item: the trace's highest-priority reason, `detail["also"]` the lower ones."""

    trace_id: str
    reason: ReviewReason
    detail: dict[str, Any]


def held_random_slots(cap: int) -> int:
    """How many of `cap` slots are reserved for random picks."""
    return math.ceil(cap / 4)


def select_items(candidates: Sequence[Candidate], cap: int) -> list[Selected]:
    """The at most `cap` items to queue, in priority order then by `sample_point`."""
    by_reason = _one_per_trace(candidates)
    random = by_reason[ReviewReason.RANDOM]
    held = min(held_random_slots(cap), len(random))
    picked: list[Selected] = []
    for reason in _NON_RANDOM:
        picked.extend(_selected(c) for c in by_reason[reason][: cap - held - len(picked)])
    picked.extend(_selected(c) for c in random[: cap - len(picked)])
    return picked


def _one_per_trace(candidates: Sequence[Candidate]) -> dict[ReviewReason, list[Candidate]]:
    """Each trace once, under its highest-priority reason, with the others folded into `also`."""
    by_trace: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_trace.setdefault(candidate.trace_id, []).append(candidate)
    by_reason: dict[ReviewReason, list[Candidate]] = {reason: [] for reason in PRIORITY}
    for matches in by_trace.values():
        ordered = sorted(matches, key=lambda c: _RANK[c.reason])
        best = ordered[0]
        also = sorted(
            {c.reason.value for c in ordered[1:] if c.reason != best.reason}, key=PRIORITY.index
        )
        by_reason[best.reason].append(
            Candidate(best.trace_id, best.reason, best.sample_point, {**best.detail, "also": also})
        )
    for matches in by_reason.values():
        matches.sort(key=lambda c: (c.sample_point, c.trace_id))
    return by_reason


def _selected(candidate: Candidate) -> Selected:
    return Selected(candidate.trace_id, candidate.reason, dict(candidate.detail))
