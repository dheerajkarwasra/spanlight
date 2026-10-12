"""The totals of an experiment run, computed from its stored items. Pure: no database, no I/O.

* `items` counts every stored item and `errors` those with an `error`;
* `cost_usd` sums the items whose cost is known; None when no item's cost is known (unknown is
  never 0). `has_unpriced` says that at least one item that made a call has no known cost (an
  unpriced model or no reported usage), so `cost_usd` is a lower bound;
* latency percentiles are over the items that answered, by nearest rank; None without any;
* a numeric score (a number or a boolean, counted as 1 or 0) reports its mean and how many items
  have it; a categorical score reports how many items fell in each category, and no mean.

The run stores its totals when it ends; they can be recomputed from the items at any time.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What the totals read of one stored item."""

    latency_ms: float | None
    cost_usd: Decimal | None
    error: str | None
    scores: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoreStat:
    mean: float | None
    count: int
    categories: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class Totals:
    items: int
    errors: int
    cost_usd: Decimal | None
    has_unpriced: bool
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    scores: dict[str, ScoreStat]


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """The nearest-rank percentile of `values` (0 < percentile ≤ 100); None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(percentile / 100 * len(ordered)), 1)
    return ordered[rank - 1]


def summarize_items(items: Sequence[ItemFacts]) -> Totals:
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
        scores=_score_stats(items),
    )


def _score_stats(items: Sequence[ItemFacts]) -> dict[str, ScoreStat]:
    numbers: dict[str, list[float]] = {}
    categories: dict[str, dict[str, int]] = {}
    for item in items:
        for name, value in item.scores.items():
            if isinstance(value, str):
                counts = categories.setdefault(name, {})
                counts[value] = counts.get(value, 0) + 1
            elif isinstance(value, bool | int | float | Decimal):
                numbers.setdefault(name, []).append(float(value))
    stats = {
        name: ScoreStat(mean=sum(values) / len(values), count=len(values))
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
        "cost_usd": None if totals.cost_usd is None else str(totals.cost_usd),
        "has_unpriced": totals.has_unpriced,
        "latency_p50_ms": totals.latency_p50_ms,
        "latency_p95_ms": totals.latency_p95_ms,
        "scores": {
            name: {"mean": stat.mean, "count": stat.count, "categories": stat.categories}
            for name, stat in totals.scores.items()
        },
    }


def totals_from_json(stored: Mapping[str, Any]) -> Totals:
    """The inverse of `totals_json`."""
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
            )
            for name, stat in (stored.get("scores") or {}).items()
        },
    )
