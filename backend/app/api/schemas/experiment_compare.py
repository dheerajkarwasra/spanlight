"""The comparison of two experiment runs. Score numbers are rounded to 4 decimals; money is a
decimal string; an unknown value is `null`, never zero."""

import uuid
from decimal import Decimal
from typing import Any

from app.api.schemas.common import ApiModel, Money
from app.evals.compare import Comparison, ItemDelta, ItemRow, ScoreDelta


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


class CompareItemOut(ApiModel):
    trace_id: str | None
    # `{text, finish_reason}`; null when the item failed.
    output: dict[str, Any] | None
    scores: dict[str, Any]
    latency_ms: float | None
    cost_usd: Money | None
    error: str | None

    @classmethod
    def of(cls, row: ItemRow) -> "CompareItemOut":
        return cls(
            trace_id=row.trace_id,
            output=None if row.output is None else dict(row.output),
            scores=dict(row.scores),
            latency_ms=row.latency_ms,
            cost_usd=row.cost_usd,
            error=row.error,
        )


class ItemDeltaOut(ApiModel):
    dataset_item_id: uuid.UUID
    # Null when the item is not in run a (or b).
    a: CompareItemOut | None
    b: CompareItemOut | None
    # `b - a` per numeric score; null for a categorical score or an item present in one run only.
    deltas: dict[str, float | None]

    @classmethod
    def of(cls, item: ItemDelta) -> "ItemDeltaOut":
        return cls(
            dataset_item_id=item.dataset_item_id,
            a=CompareItemOut.of(item.a) if item.a else None,
            b=CompareItemOut.of(item.b) if item.b else None,
            deltas={name: _number(delta) for name, delta in item.deltas.items()},
        )


class ScoreDeltaOut(ApiModel):
    # Means over all of each run's scored items (booleans count 1 or 0); null for a categorical
    # score. Higher is better, so a positive delta is an improvement.
    mean_a: float | None
    mean_b: float | None
    delta: float | None
    # Items in both runs that rose, fell or stayed the same (equal categories are unchanged;
    # different ones count in neither of the first two).
    improved: int
    regressed: int
    unchanged: int

    @classmethod
    def of(cls, score: ScoreDelta) -> "ScoreDeltaOut":
        return cls(
            mean_a=_number(score.mean_a),
            mean_b=_number(score.mean_b),
            delta=_number(score.delta),
            improved=score.improved,
            regressed=score.regressed,
            unchanged=score.unchanged,
        )


class ComparisonOut(ApiModel):
    # Items in both runs first, then those only in a, then those only in b.
    items: list[ItemDeltaOut]
    aggregate: dict[str, ScoreDeltaOut]
    # `b - a` of the runs' total cost and p95 latency; null when either side is unknown.
    cost_delta_usd: Money | None
    latency_p95_delta_ms: float | None

    @classmethod
    def of(cls, comparison: Comparison) -> "ComparisonOut":
        return cls(
            items=[ItemDeltaOut.of(item) for item in comparison.items],
            aggregate={n: ScoreDeltaOut.of(s) for n, s in comparison.aggregate.items()},
            cost_delta_usd=comparison.cost_delta_usd,
            latency_p95_delta_ms=comparison.latency_p95_delta_ms,
        )
