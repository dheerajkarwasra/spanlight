"""The comparison of two experiment runs. Score numbers are rounded to 4 decimals; money is a
decimal string; an unknown value is `null`, never zero."""

import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from app.api.schemas.common import ApiModel, Money
from app.api.schemas.experiments import Rate
from app.evals.compare import Comparison, ItemDelta, ItemRow, ScoreDelta
from app.evals.stats import ClusteredTest, Interval, PairedTest, quantize_rate


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
    # The item's 1-based rank by `dataset_item_id` in run a, else in run b.
    position: int
    # Run a's copy of the item, else run b's; null when neither run kept one.
    input: Any | None
    expected_output: Any | None
    # True when both runs kept a copy and its input or expected output differs (the pair is then
    # left out of the aggregate's counts and test); null when either run has no copy.
    changed: bool | None
    # Null when the item is not in run a (or b).
    a: CompareItemOut | None
    b: CompareItemOut | None
    # `b - a` per numeric score; null for a categorical score or an item present in one run only.
    deltas: dict[str, float | None]

    @classmethod
    def of(cls, item: ItemDelta, position: int) -> "ItemDeltaOut":
        copy = next((row for row in (item.a, item.b) if row is not None and row.has_copy), None)
        return cls(
            dataset_item_id=item.dataset_item_id,
            position=position,
            input=copy.input if copy else None,
            expected_output=copy.expected_output if copy else None,
            changed=item.changed,
            a=CompareItemOut.of(item.a) if item.a else None,
            b=CompareItemOut.of(item.b) if item.b else None,
            deltas={name: _number(delta) for name, delta in item.deltas.items()},
        )


class IntervalOut(ApiModel):
    low: Rate
    high: Rate

    @classmethod
    def of(cls, interval: Interval | None) -> "IntervalOut | None":
        return None if interval is None else cls(low=interval.low, high=interval.high)


class PairedTestOut(ApiModel):
    # `mcnemar` for a boolean score, `sign` for a numeric one.
    kind: str
    # Matched items that changed (`improved + regressed`); `unchanged` carries no information.
    n: int
    improved: int
    regressed: int
    unchanged: int
    # Exact two-sided p-value; `significant` is decided at alpha 0.05 on the exact value.
    p_value: Rate
    significant: bool

    @classmethod
    def of(cls, test: PairedTest | None) -> "PairedTestOut | None":
        if test is None:
            return None
        return cls(
            kind=test.kind,
            n=test.n,
            improved=test.improved,
            regressed=test.regressed,
            unchanged=test.unchanged,
            p_value=test.p_value,
            significant=test.significant,
        )


class ClusteredTestOut(ApiModel):
    kind: str  # `clustered_z`
    # Unchanged matched items carrying the score, and the clusters they fall in.
    n: int
    clusters: int
    # The mean of `b - a` per item, its clustered (CR0) standard error and `z` = their ratio.
    mean_difference: float
    standard_error: float
    # Null when the standard error is 0: the p-value is then undefined, never 0.
    z: float | None
    # Two-sided p from the normal approximation; `significant` is decided at alpha 0.05.
    p_value: Rate | None
    significant: bool

    @classmethod
    def of(cls, test: ClusteredTest | None) -> "ClusteredTestOut | None":
        if test is None:
            return None
        return cls(
            kind=test.kind,
            n=test.n,
            clusters=test.clusters,
            mean_difference=round(test.mean_difference, 4),
            standard_error=round(test.standard_error, 4),
            z=None if test.z is None else round(test.z, 4),
            p_value=None if test.p_value is None else quantize_rate(test.p_value),
            significant=test.significant,
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
    # Boolean scores only: each run's Wilson 95% interval on its pass rate.
    ci_a: IntervalOut | None
    ci_b: IntervalOut | None
    # Exact McNemar (boolean) or the sign test (numeric); null for a categorical score or when
    # fewer than two matched items carry the score.
    test: PairedTestOut | None
    # Only when the experiment has a `cluster_key` and `test` exists: the clustered paired z test,
    # which is then the test of record (`test` stays for reference). Null otherwise.
    clustered_test: ClusteredTestOut | None = None

    @classmethod
    def of(cls, score: ScoreDelta) -> "ScoreDeltaOut":
        return cls(
            mean_a=_number(score.mean_a),
            mean_b=_number(score.mean_b),
            delta=_number(score.delta),
            improved=score.improved,
            regressed=score.regressed,
            unchanged=score.unchanged,
            ci_a=IntervalOut.of(score.ci_a),
            ci_b=IntervalOut.of(score.ci_b),
            test=PairedTestOut.of(score.test),
            clustered_test=ClusteredTestOut.of(score.clustered_test),
        )


class ComparisonOut(ApiModel):
    # Items in both runs first, then those only in a, then those only in b.
    items: list[ItemDeltaOut]
    aggregate: dict[str, ScoreDeltaOut]
    # `b - a` of the runs' total cost and p95 latency; null when either side is unknown.
    cost_delta_usd: Money | None
    latency_p95_delta_ms: float | None
    # Pairs whose item was edited between the runs; they are left out of the aggregate's counts.
    changed_items: int

    @classmethod
    def of(cls, comparison: Comparison) -> "ComparisonOut":
        items = comparison.items
        rank_a = _ranks(item.dataset_item_id for item in items if item.a is not None)
        rank_b = _ranks(item.dataset_item_id for item in items if item.b is not None)
        return cls(
            items=[
                ItemDeltaOut.of(
                    item, rank_a.get(item.dataset_item_id) or rank_b[item.dataset_item_id]
                )
                for item in items
            ],
            aggregate={n: ScoreDeltaOut.of(s) for n, s in comparison.aggregate.items()},
            cost_delta_usd=comparison.cost_delta_usd,
            latency_p95_delta_ms=comparison.latency_p95_delta_ms,
            changed_items=comparison.changed_items,
        )


def _ranks(ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Each id's 1-based rank in id order."""
    return {key: rank for rank, key in enumerate(sorted(ids), start=1)}
