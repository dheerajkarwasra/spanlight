"""Latency histogram buckets for hourly rollups.

A rollup row cannot keep every duration, so it keeps a fixed-size histogram
instead: 32 buckets whose upper bounds grow geometrically from 1 ms to
300 000 ms (300 s). Each bound is about 1.5x the previous one. Two histograms
for the same dimensions add element-wise, so any number of hours can be merged
into one without going back to the raw spans.

Percentiles read from a histogram are an approximation. Continuous percentile
ranks identify the two contributing order statistics. Each bucket's harmonic
midpoint minimizes its worst-case relative error; interpolating these estimates
preserves the bound, about 20.066 % for bounded interior buckets. The
first bucket has no positive lower bound and the last is open-ended, so that
relative guarantee excludes both extremes. Responses remain labelled
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

    Like ``percentile_cont``, interpolate the observations around ``q * (n - 1)``.
    Only their bucket intervals survive storage: interpolate the buckets'
    minimax relative estimates. At a zero lower bound use the absolute-error
    midpoint instead. The last bucket uses its stored cap;
    true overflow values cannot be recovered from the histogram.

    Returns ``None`` when the histogram is empty, so an unknown percentile is
    never reported as ``0``.

    Raises:
        ValueError: ``q`` is outside ``[0, 1]`` (or NaN), ``counts`` does not have
            ``BUCKET_COUNT`` entries, or a count is not a nonnegative integer.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be between 0 and 1, got {q}")
    _require_length(counts)
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("bucket counts must be nonnegative integers")
    total = sum(counts)
    if total == 0:
        return None

    # Keep order-statistic ranks exact even when accumulated counts exceed 2**53.
    numerator, denominator = float(q).as_integer_ratio()
    rank, remainder = divmod((total - 1) * numerator, denominator)
    lower = _rank_estimate(counts, rank)
    upper = _rank_estimate(counts, rank + bool(remainder))
    return lower + (remainder / denominator) * (upper - lower)


def merge(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Add two histograms element-wise.

    Raises:
        ValueError: either histogram does not have ``BUCKET_COUNT`` entries.
    """
    _require_length(a)
    _require_length(b)
    return [x + y for x, y in zip(a, b, strict=True)]


def _rank_estimate(counts: Sequence[int], rank: int) -> float:
    """Monotone bucket representative of a zero-based order statistic."""
    cumulative = 0
    for index, count in enumerate(counts):
        cumulative += count
        if rank < cumulative:
            if index == 0:
                return BOUNDS_MS[0] / 2
            lower, upper = BOUNDS_MS[index - 1], BOUNDS_MS[index]
            return 2 * lower * upper / (lower + upper)
    raise ValueError("rank is outside the histogram")


def _require_length(counts: Sequence[int]) -> None:
    if len(counts) != BUCKET_COUNT:
        raise ValueError(f"a histogram has {BUCKET_COUNT} buckets, got {len(counts)}")
