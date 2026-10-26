"""Compare two experiment runs. Pure: no database, no I/O.

* Items are matched on `dataset_item_id`. Matched items come first, in id order; items present in
  only one run follow (those of `a`, then those of `b`) with a null other side and null deltas.
* A pair is `changed` when both rows carry a frozen copy and their `input` or `expected_output`
  differ as canonical JSON (so `true` and `1` differ); `None` when either is a legacy row without
  a copy (unknown, compared as before). Item metadata is not compared: evaluators never see it.
  A changed pair has a `None` delta for every score and is left out of `improved`, `regressed`,
  `unchanged` and the paired test, since scores against different references are not a fair
  pair; `changed_items` counts these pairs.
* A numeric score (a number, or a boolean counted as 1 or 0) has the delta `b - a` at 4 decimals.
  Every Phase 5 score is higher-is-better, so a positive delta is an improvement.
* A categorical score has no delta: equal values are `unchanged`, different ones count as neither
  improved nor regressed.
* The aggregate per score name counts the unchanged matched items that improved, regressed or
  stayed the same, and compares the two runs' means over all their scored items (changed ones
  included).
* A boolean score (its config says so, or every value is a `bool`) carries each run's Wilson
  interval and the exact McNemar test on the matched items; a numeric score carries the paired
  sign test; a categorical score has no test. Fewer than two matched items carrying the score
  give no test either.
* When the experiment groups items by a metadata key (each `ItemRow` carries a `cluster`), a
  boolean or numeric score also carries the clustered paired z test on `b - a` over the same
  unchanged matched pairs, clustered by run a's copy; it is then the test of record, and McNemar or
  the sign test stays alongside for reference.
* `cost_delta_usd` and `latency_p95_delta_ms` are `b - a` of the runs' totals and are None when
  either side is unknown (unknown is never 0).

Comparing a run with itself gives zero deltas.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.evals.experiment_totals import ItemFacts, ScoreStat, Totals, summarize_items
from app.evals.stats import (
    ClusteredTest,
    Interval,
    PairedTest,
    clustered_paired_test,
    mcnemar_exact,
    sign_test,
)

PLACES = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class ItemRow:
    """What the comparison reads of one stored experiment item."""

    dataset_item_id: UUID
    trace_id: str | None
    output: Mapping[str, Any] | None
    scores: Mapping[str, Any]
    latency_ms: float | None
    cost_usd: Decimal | None
    error: str | None
    evaluator_error: str | None
    # The item as the run saw it; `has_copy` is False on a legacy row stored before runs kept one.
    input: Any = None
    expected_output: Any = None
    has_copy: bool = False
    # `experiment_totals.cluster_label` with the experiment's cluster key; None without one.
    cluster: str | UUID | None = None


@dataclass(frozen=True, slots=True)
class ItemDelta:
    dataset_item_id: UUID
    a: ItemRow | None
    b: ItemRow | None
    # Per score name of either side; None where there is no numeric delta (one side missing, or
    # a categorical score).
    deltas: dict[str, Decimal | None]
    # True when both sides have a copy and it differs; None when either side has none or the
    # item is in one run only.
    changed: bool | None


@dataclass(frozen=True, slots=True)
class ScoreDelta:
    mean_a: Decimal | None
    mean_b: Decimal | None
    delta: Decimal | None
    improved: int
    regressed: int
    unchanged: int
    # Boolean scores only: each run's Wilson interval on its pass rate.
    ci_a: Interval | None = None
    ci_b: Interval | None = None
    # McNemar for a boolean score, the sign test for a numeric one; None for a categorical score
    # or fewer than two matched items.
    test: PairedTest | None = None
    # With a cluster key, where `test` exists: the clustered paired z test, the test of record.
    clustered_test: ClusteredTest | None = None


@dataclass(frozen=True, slots=True)
class Comparison:
    items: list[ItemDelta]
    aggregate: dict[str, ScoreDelta]
    cost_delta_usd: Decimal | None
    latency_p95_delta_ms: float | None
    changed_items: int


def _number(value: Any) -> Decimal | None:
    """A numeric score as a Decimal; None for a category or anything else."""
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    return None


def _delta(a: Any, b: Any) -> Decimal | None:
    low, high = _number(a), _number(b)
    if low is None or high is None:
        return None
    return (high - low).quantize(PLACES)


def _changed(a: ItemRow | None, b: ItemRow | None) -> bool | None:
    if a is None or b is None or not (a.has_copy and b.has_copy):
        return None
    return not (_same(a.input, b.input) and _same(a.expected_output, b.expected_output))


def _same(a: Any, b: Any) -> bool:
    """JSON equality: Python's `==` would call `true` and `1` equal."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _item_deltas(
    a: ItemRow | None, b: ItemRow | None, changed: bool | None
) -> dict[str, Decimal | None]:
    scores_a = a.scores if a else {}
    scores_b = b.scores if b else {}
    names = sorted(scores_a.keys() | scores_b.keys())
    if a is None or b is None or changed:
        return dict.fromkeys(names)
    return {name: _delta(scores_a.get(name), scores_b.get(name)) for name in names}


def _mean(rows: Sequence[ItemRow], name: str) -> Decimal | None:
    values = [n for row in rows if (n := _number(row.scores.get(name))) is not None]
    if not values:
        return None
    return (sum(values, Decimal(0)) / len(values)).quantize(PLACES)


def _interval(stat: ScoreStat | None) -> Interval | None:
    if stat is None or stat.ci_low is None or stat.ci_high is None:
        return None
    return Interval(low=stat.ci_low, high=stat.ci_high)


def _paired_test(
    stats: Sequence[ScoreStat], improved: int, regressed: int, unchanged: int
) -> PairedTest | None:
    if any(s.categories is not None for s in stats) or improved + regressed + unchanged < 2:
        return None
    if all(s.passed is not None for s in stats):
        return mcnemar_exact(regressed, improved, unchanged=unchanged)
    return sign_test(improved, regressed, unchanged=unchanged)


def _score_delta(
    name: str,
    a: Sequence[ItemRow],
    b: Sequence[ItemRow],
    totals: tuple[Totals, Totals],
    changed: frozenset[UUID],
) -> ScoreDelta:
    mean_a, mean_b = _mean(a, name), _mean(b, name)
    stat_a, stat_b = (t.scores.get(name) for t in totals)
    # A changed pair is no pair: leaving it out of `by_id` skips it below.
    by_id = {row.dataset_item_id: row for row in b if row.dataset_item_id not in changed}
    improved = regressed = unchanged = 0
    before_values: list[Decimal] = []
    after_values: list[Decimal] = []
    clusters: list[str | UUID | None] = []
    for row in a:
        other = by_id.get(row.dataset_item_id)
        if other is None or name not in row.scores or name not in other.scores:
            continue
        before, after = row.scores[name], other.scores[name]
        low, high = _number(before), _number(after)
        if low is None or high is None:  # categorical: equal is unchanged, different is neither
            if before == after:
                unchanged += 1
            continue
        before_values.append(low)
        after_values.append(high)
        clusters.append(row.cluster)
        delta = (high - low).quantize(PLACES)
        if delta > 0:
            improved += 1
        elif delta < 0:
            regressed += 1
        else:
            unchanged += 1
    test = _paired_test(
        [s for s in (stat_a, stat_b) if s is not None], improved, regressed, unchanged
    )
    clustered = test is not None and all(cluster is not None for cluster in clusters)
    return ScoreDelta(
        mean_a=mean_a,
        mean_b=mean_b,
        delta=None if mean_a is None or mean_b is None else (mean_b - mean_a).quantize(PLACES),
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
        ci_a=_interval(stat_a),
        ci_b=_interval(stat_b),
        test=test,
        clustered_test=(
            clustered_paired_test(before_values, after_values, clusters) if clustered else None
        ),
    )


def _facts(rows: Sequence[ItemRow]) -> list[ItemFacts]:
    return [
        ItemFacts(row.latency_ms, row.cost_usd, row.error, row.scores, row.evaluator_error)
        for row in rows
    ]


def _difference[T: (Decimal, float)](a: T | None, b: T | None) -> T | None:
    return None if a is None or b is None else b - a


def compare_runs(
    a: Sequence[ItemRow], b: Sequence[ItemRow], *, boolean_scores: frozenset[str] = frozenset()
) -> Comparison:
    """Compare the items of run `a` with those of run `b`; `boolean_scores` names the scores whose
    config is boolean (their 0 / 1 numbers then get McNemar, not the sign test)."""
    rows_a = {row.dataset_item_id: row for row in a}
    rows_b = {row.dataset_item_id: row for row in b}
    matched = sorted(rows_a.keys() & rows_b.keys())
    only_a = sorted(rows_a.keys() - rows_b.keys())
    only_b = sorted(rows_b.keys() - rows_a.keys())
    items = []
    for key in [*matched, *only_a, *only_b]:
        row_a, row_b = rows_a.get(key), rows_b.get(key)
        changed = _changed(row_a, row_b)
        items.append(ItemDelta(key, row_a, row_b, _item_deltas(row_a, row_b, changed), changed))
    changed_ids = frozenset(item.dataset_item_id for item in items if item.changed)
    names = sorted({name for row in [*a, *b] for name in row.scores})
    totals = (
        summarize_items(_facts(a), boolean_scores=boolean_scores),
        summarize_items(_facts(b), boolean_scores=boolean_scores),
    )
    return Comparison(
        items=items,
        aggregate={name: _score_delta(name, a, b, totals, changed_ids) for name in names},
        cost_delta_usd=_difference(totals[0].cost_usd, totals[1].cost_usd),
        latency_p95_delta_ms=_difference(totals[0].latency_p95_ms, totals[1].latency_p95_ms),
        changed_items=len(changed_ids),
    )
