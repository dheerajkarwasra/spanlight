"""Latency histogram buckets for hourly rollups.

A rollup row cannot keep every duration, so it keeps a fixed-size histogram
instead: 32 buckets whose upper bounds grow geometrically from 1 ms to
300 000 ms (300 s). Each bound is about 1.5x the previous one. Two histograms
for the same dimensions add element-wise, so any number of hours can be merged
into one without going back to the raw spans.

Percentiles read from a histogram are an approximation. Everything that falls
into a bucket is assumed to be spread evenly across it, and the answer is
interpolated linearly between the bucket's edges. Because a bucket is about
1.5x wide, the estimate is within roughly 25 % of the true value, which is
enough for a dashboard chart and is why those responses are labelled
approximate. A window that reads no rollup hour (in practice the last hour)
reads the raw spans and stays exact.

Bucket ``i`` holds the durations ``d`` with ``BOUNDS_MS[i-1] < d <= BOUNDS_MS[i]``
(bucket 0 holds everything up to and including ``BOUNDS_MS[0]``). The last
bucket also holds every duration above ``BOUNDS_MS[-1]``, so it is open-ended
and its percentile estimates are clamped to ``BOUNDS_MS[-1]``.

The rollup job computes the bucket index in SQL, so the SQL and
:func:`bucket_index` must agree on every value: the index is the number of
bounds strictly less than the duration, capped at ``BUCKET_COUNT - 1``. The
SQL must take its bounds from :data:`BOUNDS_MS` (as a bound parameter, negated
and reversed for ``width_bucket``; see ``app.rollups.compute``) instead of
recomputing them, so the two cannot drift apart.

This module is pure: no I/O, no settings, no database.
"""

from bisect import bisect_left
from collections.abc import Sequence
from math import isnan

BUCKET_COUNT = 32
MIN_BOUND_MS = 1.0
MAX_BOUND_MS = 300_000.0


def _build_bounds() -> tuple[float, ...]:
    """Log-spaced upper bounds: ``MAX_BOUND_MS ** (i / (BUCKET_COUNT - 1))``.

    The end points are set explicitly so they are exactly ``1.0`` and
    ``300000.0`` whatever the platform's ``pow`` does with the exponent.
    """
    last = BUCKET_COUNT - 1
    bounds = [MAX_BOUND_MS ** (i / last) for i in range(BUCKET_COUNT)]
    bounds[0] = MIN_BOUND_MS
    bounds[last] = MAX_BOUND_MS
    return tuple(bounds)


BOUNDS_MS: tuple[float, ...] = _build_bounds()
"""Upper bound of each bucket in milliseconds, strictly increasing, 1 ms to 300 000 ms."""


def bucket_index(ms: float) -> int:
    """Return the bucket that holds a duration of ``ms`` milliseconds.

    This is the first ``i`` with ``ms <= BOUNDS_MS[i]``, which is the same as the
    number of bounds strictly less than ``ms``, capped at the last bucket. Values
    at or below the first bound (including zero and negatives) land in bucket 0
    and values above the last bound land in bucket ``BUCKET_COUNT - 1``. The SQL
    in the rollup job must compute ``least(31, count of bounds < value)``, which it does as
    ``least(31, 32 - width_bucket(-value, negated bounds in reverse order))``.

    Raises:
        ValueError: ``ms`` is NaN, which has no position on the scale.
    """
    if isnan(ms):
        raise ValueError("duration must not be NaN")
    return min(bisect_left(BOUNDS_MS, ms), BUCKET_COUNT - 1)


def approx_percentile(counts: Sequence[int], q: float) -> float | None:
    """Estimate the ``q`` quantile (0 to 1) of the durations behind a histogram.

    The rank ``q * total`` is located in the bucket that holds it, and the result
    is interpolated linearly between that bucket's lower edge (0 ms for the first
    bucket, otherwise the previous bound) and its upper bound. The result is never
    above ``MAX_BOUND_MS``: the last bucket is open-ended, so its estimate is
    capped at the last bound. The error of the estimate is bounded by the width
    of one bucket, about 25 % of the value (see the module docstring).

    Returns ``None`` when the histogram is empty, so an unknown percentile is
    never reported as ``0``.

    Raises:
        ValueError: ``q`` is outside ``[0, 1]`` (or NaN), ``counts`` does not have
            ``BUCKET_COUNT`` entries, or a count is negative.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be between 0 and 1, got {q}")
    _require_length(counts)
    if any(count < 0 for count in counts):
        raise ValueError("bucket counts must not be negative")
    total = sum(counts)
    if total == 0:
        return None

    rank = q * total
    cumulative = 0
    last_filled = 0
    for index, count in enumerate(counts):
        if count == 0:
            continue
        last_filled = index
        if cumulative + count >= rank:
            return _interpolate(index, (rank - cumulative) / count)
        cumulative += count
    # Only reachable when q * total rounds above total (counts beyond 2**53):
    # the quantile is then the top of the last bucket that has data.
    return BOUNDS_MS[last_filled]


def merge(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Add two histograms element-wise.

    Raises:
        ValueError: either histogram does not have ``BUCKET_COUNT`` entries.
    """
    _require_length(a)
    _require_length(b)
    return [x + y for x, y in zip(a, b, strict=True)]


def _interpolate(index: int, fraction: float) -> float:
    """Point ``fraction`` (0 to 1) of the way through bucket ``index``."""
    lower = 0.0 if index == 0 else BOUNDS_MS[index - 1]
    upper = BOUNDS_MS[index]
    return min(lower + min(max(fraction, 0.0), 1.0) * (upper - lower), MAX_BOUND_MS)


def _require_length(counts: Sequence[int]) -> None:
    if len(counts) != BUCKET_COUNT:
        raise ValueError(f"a histogram has {BUCKET_COUNT} buckets, got {len(counts)}")
