"""Failure-category rates per release, from counted labels. Pure: no database, no I/O.

A trace counts once, under its latest `failure_category` score. `reviewed` is the number of
such traces of a release in the window; `rate` is `count / reviewed` at 4 places and the interval
is the Wilson 95 % interval of `count` of `reviewed` (`app.evals.stats`). A release nobody
reviewed has no rates at all (unknown, never 0 %). Labels come in the config's order first; a
label no longer in the config (the list was edited) follows, alphabetically, under its own name.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from app.evals.stats import quantize_rate, wilson_interval


@dataclass(frozen=True, slots=True)
class LabelCount:
    """Traces of one release whose latest failure category is `label`."""

    release: str | None
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class ReviewedCount:
    """Traces of one release that carry a failure category at all."""

    release: str | None
    reviewed: int


@dataclass(frozen=True, slots=True)
class CategoryRate:
    count: int
    rate: Decimal
    ci_low: Decimal
    ci_high: Decimal


@dataclass(frozen=True, slots=True)
class ReleaseRates:
    release: str | None
    reviewed: int
    rates: dict[str, CategoryRate]


def _rate(count: int, reviewed: int) -> CategoryRate:
    interval = wilson_interval(count, reviewed)
    if interval is None:  # unreachable: callers pass reviewed > 0
        raise ValueError("reviewed must be positive")
    return CategoryRate(
        count=count,
        rate=quantize_rate(Fraction(count, reviewed)),
        ci_low=interval.low,
        ci_high=interval.high,
    )


def category_rates(
    configured: Sequence[str],
    releases: Sequence[str | None],
    reviewed: Sequence[ReviewedCount],
    counts: Sequence[LabelCount],
) -> list[ReleaseRates]:
    """One entry per release in `releases`, in that order, then one for `release: None` when
    traces without a release were seen (a `ReviewedCount` for `None` exists)."""
    reviewed_by = {entry.release: entry.reviewed for entry in reviewed}
    counts_by: dict[str | None, dict[str, int]] = {}
    for entry in counts:
        counts_by.setdefault(entry.release, {})[entry.label] = entry.count
    order = list(releases)
    if None in reviewed_by and None not in order:
        order.append(None)
    result: list[ReleaseRates] = []
    for release in order:
        n = reviewed_by.get(release, 0)
        seen = counts_by.get(release, {})
        labels = [*configured, *sorted(label for label in seen if label not in configured)]
        rates = {label: _rate(seen.get(label, 0), n) for label in labels} if n else {}
        result.append(ReleaseRates(release=release, reviewed=n, rates=rates))
    return result
