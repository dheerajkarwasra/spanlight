"""When a trace's cost is an outlier among traces of the same name. Pure.

The threshold is `percentile_disc(0.99)` of the known costs over the 7-day group, an observed value
rather than an interpolated one, so the same rows give the same threshold in SQL and here. A group
with fewer than `OUTLIER_MIN_SAMPLE` known costs has no threshold, and an unknown cost is never an
outlier (unknown is not zero).
"""

import math
from collections.abc import Sequence
from decimal import Decimal

OUTLIER_MIN_SAMPLE = 30
OUTLIER_FRACTION = Decimal("0.99")


def percentile_disc(values: Sequence[Decimal], fraction: Decimal) -> Decimal | None:
    """Postgres `percentile_disc`: the first sorted value with cumulative share >= `fraction`."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def outlier_threshold(costs: Sequence[Decimal | None]) -> Decimal | None:
    """The p99 of the known costs, or `None` when fewer than `OUTLIER_MIN_SAMPLE` are known."""
    known = [cost for cost in costs if cost is not None]
    if len(known) < OUTLIER_MIN_SAMPLE:
        return None
    return percentile_disc(known, OUTLIER_FRACTION)


def is_outlier(cost: Decimal | None, threshold: Decimal | None) -> bool:
    """Strictly above the threshold; a group where every cost is equal picks nothing."""
    return cost is not None and threshold is not None and cost > threshold
