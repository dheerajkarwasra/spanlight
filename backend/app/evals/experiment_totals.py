"""The totals of an experiment run, computed from its stored items. Pure: no database, no I/O.

* `items` counts every stored item, `errors` those with an `error` and `evaluator_errors` those
  whose scoring broke (`evaluator_error`);
* `cost_usd` sums the items whose cost is known; None when no item's cost is known (unknown is
  never 0). `has_unpriced` says that at least one item that made a call has no known cost (an
  unpriced model or no reported usage), so `cost_usd` is a lower bound;
* latency percentiles are over the items that answered, by nearest rank; None without any;
* a numeric score (a number or a boolean, counted as 1 or 0) reports its mean and how many items
  have it; a categorical score reports how many items fell in each category, and no mean;
* a boolean score (its config says so, or every value is a `bool`) also reports how many items
  passed and the Wilson 95 % interval on that rate; numeric and categorical scores carry None.
  When the experiment groups items by a metadata key (`cluster_key`), a boolean score also carries
  the clustered 95 % interval (`stats.clustered_mean_interval`) and its number of clusters; without
  a key, and on totals stored before these fields, they are None.

The run stores its totals when it ends; they can be recomputed from the items at any time.
"""

import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.evals.stats import Interval, clustered_mean_interval, wilson_interval


def cluster_label(value: Any, item_id: uuid.UUID) -> str | uuid.UUID:
    """An item's cluster under the experiment's cluster key: the key's value in the item's frozen
    metadata as JSON text, or the item's own id (a cluster of one) when the copy lacks the key."""
    return item_id if value is None else json.dumps(value, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What the totals read of one stored item."""

    latency_ms: float | None
    cost_usd: Decimal | None
    error: str | None
    scores: Mapping[str, Any]
    evaluator_error: str | None
    # `cluster_label` when the experiment has a cluster key; None when it has none.
    cluster: str | uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ScoreStat:
    mean: float | None
    count: int
    categories: dict[str, int] | None = None
    # Boolean scores only: items that passed and the Wilson interval on the pass rate.
    passed: int | None = None
    ci_low: Decimal | None = None
    ci_high: Decimal | None = None
    # Boolean scores with a cluster key only: the clustered interval and its number of clusters.
    ci_clustered_low: Decimal | None = None
    ci_clustered_high: Decimal | None = None
    clusters: int | None = None


@dataclass(frozen=True, slots=True)
class Totals:
    items: int
    errors: int
    cost_usd: Decimal | None
    has_unpriced: bool
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    scores: dict[str, ScoreStat]
    evaluator_errors: int


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """The nearest-rank percentile of `values` (0 < percentile ≤ 100); None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(percentile / 100 * len(ordered)), 1)
    return ordered[rank - 1]


def summarize_items(
    items: Sequence[ItemFacts], *, boolean_scores: frozenset[str] = frozenset()
) -> Totals:
    """`boolean_scores` names the scores whose config is boolean; their 0 / 1 numbers (an old SDK
    stores booleans that way) get the pass count and interval too."""
    latencies = [i.latency_ms for i in items if i.error is None and i.latency_ms is not None]
    costs = [i.cost_usd for i in items if i.cost_usd is not None]
    return Totals(
        items=len(items),
        errors=sum(1 for item in items if item.error is not None),
        cost_usd=sum(costs, Decimal(0)) if costs else None,
        # An item that made a call has a latency; one that never called (a missing variable) has
        # no cost to know.
        has_unpriced=any(i.latency_ms is not None and i.cost_usd is None for i in items),
        latency_p50_ms=nearest_rank(latencies, 50),
        latency_p95_ms=nearest_rank(latencies, 95),
        scores=_score_stats(items, boolean_scores),
        evaluator_errors=sum(1 for item in items if item.evaluator_error is not None),
    )


def _numeric_stat(
    values: Sequence[Any], clusters: Sequence[str | uuid.UUID | None], *, boolean: bool
) -> ScoreStat:
    numbers = [float(value) for value in values]
    mean = sum(numbers) / len(numbers)
    if not boolean:
        return ScoreStat(mean=mean, count=len(numbers))
    passed = sum(1 for number in numbers if number == 1)
    interval = wilson_interval(passed, len(numbers))
    clustered: Interval | None = None
    count: int | None = None
    if all(cluster is not None for cluster in clusters):
        clustered, count = clustered_mean_interval(numbers, clusters)
    return ScoreStat(
        mean=mean,
        count=len(numbers),
        passed=passed,
        ci_low=None if interval is None else interval.low,
        ci_high=None if interval is None else interval.high,
        ci_clustered_low=None if clustered is None else clustered.low,
        ci_clustered_high=None if clustered is None else clustered.high,
        clusters=count,
    )


def _score_stats(
    items: Sequence[ItemFacts], boolean_scores: frozenset[str]
) -> dict[str, ScoreStat]:
    numbers: dict[str, list[Any]] = {}
    clusters: dict[str, list[str | uuid.UUID | None]] = {}
    categories: dict[str, dict[str, int]] = {}
    for item in items:
        for name, value in item.scores.items():
            if isinstance(value, str):
                counts = categories.setdefault(name, {})
                counts[value] = counts.get(value, 0) + 1
            elif isinstance(value, bool | int | float | Decimal):
                numbers.setdefault(name, []).append(value)
                clusters.setdefault(name, []).append(item.cluster)
    stats = {
        name: _numeric_stat(
            values,
            clusters[name],
            boolean=name in boolean_scores or all(isinstance(v, bool) for v in values),
        )
        for name, values in numbers.items()
    }
    for name, counts in categories.items():
        if name not in stats:  # a name is one type; the first seen wins if items disagree
            stats[name] = ScoreStat(mean=None, count=sum(counts.values()), categories=counts)
    return dict(sorted(stats.items()))


def totals_json(totals: Totals) -> dict[str, Any]:
    """`totals` as stored in `experiment_runs.totals`; money is a decimal string."""
    return {
        "items": totals.items,
        "errors": totals.errors,
        "evaluator_errors": totals.evaluator_errors,
        "cost_usd": None if totals.cost_usd is None else str(totals.cost_usd),
        "has_unpriced": totals.has_unpriced,
        "latency_p50_ms": totals.latency_p50_ms,
        "latency_p95_ms": totals.latency_p95_ms,
        "scores": {
            name: {
                "mean": stat.mean,
                "count": stat.count,
                "categories": stat.categories,
                "passed": stat.passed,
                "ci_low": None if stat.ci_low is None else str(stat.ci_low),
                "ci_high": None if stat.ci_high is None else str(stat.ci_high),
                "ci_clustered_low": (
                    None if stat.ci_clustered_low is None else str(stat.ci_clustered_low)
                ),
                "ci_clustered_high": (
                    None if stat.ci_clustered_high is None else str(stat.ci_clustered_high)
                ),
                "clusters": stat.clusters,
            }
            for name, stat in totals.scores.items()
        },
    }


def _rate(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def totals_from_json(stored: Mapping[str, Any], evaluator_errors: int | None = None) -> Totals:
    """The inverse of `totals_json`. Totals stored before the pass count and the intervals (Wilson,
    clustered) existed lack those keys and read as None. Totals stored before `evaluator_errors`
    existed lack it: the caller counts it from the run's items (`needs_evaluator_errors`) and
    passes it in."""
    if "evaluator_errors" in stored:
        evaluator_errors = int(stored["evaluator_errors"])
    elif evaluator_errors is None:
        raise ValueError("stored totals lack evaluator_errors; count them from the items")
    cost = stored.get("cost_usd")
    return Totals(
        items=int(stored.get("items", 0)),
        errors=int(stored.get("errors", 0)),
        cost_usd=None if cost is None else Decimal(str(cost)),
        has_unpriced=bool(stored.get("has_unpriced", False)),
        latency_p50_ms=stored.get("latency_p50_ms"),
        latency_p95_ms=stored.get("latency_p95_ms"),
        scores={
            name: ScoreStat(
                mean=stat.get("mean"),
                count=int(stat.get("count", 0)),
                categories=stat.get("categories"),
                passed=stat.get("passed"),
                ci_low=_rate(stat.get("ci_low")),
                ci_high=_rate(stat.get("ci_high")),
                ci_clustered_low=_rate(stat.get("ci_clustered_low")),
                ci_clustered_high=_rate(stat.get("ci_clustered_high")),
                clusters=stat.get("clusters"),
            )
            for name, stat in (stored.get("scores") or {}).items()
        },
        evaluator_errors=evaluator_errors,
    )


def needs_evaluator_errors(stored: Mapping[str, Any] | None) -> bool:
    """Stored totals from before `evaluator_errors` existed."""
    return stored is not None and "evaluator_errors" not in stored
