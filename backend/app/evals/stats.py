"""Statistics for evaluation results: a Wilson interval on a pass rate, the exact McNemar test for
two runs scored on the same items, the paired sign test for numeric scores, and their clustered
versions for items that are not independent (a cluster-robust mean interval and a paired z test).
Pure: no database, no I/O.

The Wilson interval is computed in floating point from exact integers and then quantized; the
paired tests use an exact binomial in `Fraction`, so `significant` is decided on the exact value,
never on the rounded one. No continuity correction anywhere. Rates are 4 places, `ROUND_HALF_EVEN`,
clamped to `[0, 1]`; `n = 0` gives no interval (unknown, never `[0, 1]`).

The clustered statistics use the CR0 standard error (Miller 2024, arXiv:2411.00640, Eq. 4 and
Eq. 8, with the CLT term over n rather than n - 1): `SE = sqrt(sum_c (sum_{i in c} (s_i - mean))^2)
/ n`. Items that share a cluster label are one cluster; give an item a label of its own to make it
a cluster of one. The sums are exact (`Fraction`), so a zero spread is exactly zero.
"""

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Literal

PLACES = Decimal("0.0001")
Z_95 = Decimal("1.959964")
ALPHA = Decimal("0.05")

_ZERO = Decimal(0)
_ONE = Decimal(1)
_ALPHA_FRACTION = Fraction(1, 20)


@dataclass(frozen=True, slots=True)
class Interval:
    low: Decimal
    high: Decimal


@dataclass(frozen=True, slots=True)
class PairedTest:
    kind: Literal["mcnemar", "sign"]
    n: int
    improved: int
    regressed: int
    unchanged: int
    p_value: Decimal
    significant: bool


@dataclass(frozen=True, slots=True)
class ClusteredTest:
    """The clustered paired z test on per-item differences `b - a` (normal approximation)."""

    kind: Literal["clustered_z"]
    n: int
    clusters: int
    mean_difference: float
    standard_error: float
    # None when the standard error is 0 (every cluster agrees exactly): undefined, never 0.
    z: float | None
    p_value: float | None
    significant: bool


def quantize_rate(value: float | Fraction) -> Decimal:
    """Round a rate to 4 places, half-even, and clamp it to `[0, 1]`."""
    if isinstance(value, Fraction):
        exact = Decimal(value.numerator) / Decimal(value.denominator)
    else:
        # repr, not the binary float: 0.00015 must round as the decimal it was written as.
        exact = Decimal(repr(value))
    return min(_ONE, max(_ZERO, exact)).quantize(PLACES, rounding=ROUND_HALF_EVEN)


def wilson_interval(successes: int, n: int, *, z: Decimal = Z_95) -> Interval | None:
    """Wilson score interval on `successes / n`; `None` when `n = 0`."""
    if n <= 0:
        return None
    zf = float(z)
    p = successes / n
    denominator = 1 + zf * zf / n
    centre = (p + zf * zf / (2 * n)) / denominator
    half = (zf / denominator) * math.sqrt(p * (1 - p) / n + zf * zf / (4 * n * n))
    return Interval(low=quantize_rate(centre - half), high=quantize_rate(centre + half))


def exact_binomial_two_sided(k: int, n: int) -> Fraction:
    """Two-sided exact binomial p-value for `k` of `n` under p = 1/2, capped at 1."""
    if n <= 0:
        return Fraction(1)
    tail = sum(math.comb(n, i) for i in range(min(k, n - k) + 1))
    return min(Fraction(1), 2 * Fraction(tail, 2**n))


def _paired(
    kind: Literal["mcnemar", "sign"], improved: int, regressed: int, unchanged: int
) -> PairedTest:
    n = improved + regressed
    p = exact_binomial_two_sided(regressed, n)
    return PairedTest(
        kind=kind,
        n=n,
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
        p_value=quantize_rate(p),
        significant=p < _ALPHA_FRACTION,
    )


def mcnemar_exact(b: int, c: int, *, unchanged: int = 0) -> PairedTest:
    """Exact McNemar test: `b` = pass in A and fail in B (regressed), `c` = fail in A and pass in B
    (improved). Concordant pairs are `unchanged` and carry no information."""
    return _paired("mcnemar", improved=c, regressed=b, unchanged=unchanged)


def sign_test(improved: int, regressed: int, *, unchanged: int = 0) -> PairedTest:
    """Paired sign test on numeric scores: ties are dropped, the same exact binomial decides."""
    return _paired("sign", improved=improved, regressed=regressed, unchanged=unchanged)


def _clustered(
    values: Sequence[Fraction | Decimal | float | int], clusters: Sequence[Hashable]
) -> tuple[Fraction, float, int]:
    """The mean, the CR0 standard error and the number of clusters; `values` is not empty."""
    exact = [Fraction(value) for value in values]
    mean = sum(exact, Fraction(0)) / len(exact)
    sums: dict[Hashable, Fraction] = {}
    for value, cluster in zip(exact, clusters, strict=True):
        sums[cluster] = sums.get(cluster, Fraction(0)) + value - mean
    spread = sum((total * total for total in sums.values()), Fraction(0))
    return mean, math.sqrt(spread) / len(exact), len(sums)


def clustered_mean_interval(
    values: Sequence[Decimal | float | int], clusters: Sequence[Hashable], *, z: Decimal = Z_95
) -> tuple[Interval | None, int]:
    """The clustered 95 % interval `mean ± z · SE` on a pass rate (clamped to `[0, 1]`) and the
    number of clusters. No interval without values, nor from one cluster, whose spread around the
    mean is always 0."""
    if not values:
        return None, 0
    mean, se, count = _clustered(values, clusters)
    if count < 2:
        return None, count
    centre, half = float(mean), float(z) * se
    return Interval(low=quantize_rate(centre - half), high=quantize_rate(centre + half)), count


def clustered_paired_test(
    a: Sequence[Decimal | float | int],
    b: Sequence[Decimal | float | int],
    clusters: Sequence[Hashable],
) -> ClusteredTest | None:
    """The clustered paired test on matched items `a[i]`, `b[i]` (`d_i = b_i - a_i`): `z = mean(d)
    / SE_d`, two-sided normal p. None with fewer than two pairs."""
    if len(a) < 2:
        return None
    differences = [Fraction(after) - Fraction(before) for before, after in zip(a, b, strict=True)]
    mean, se, count = _clustered(differences, clusters)
    z = None if se == 0 else float(mean) / se
    p = None if z is None else math.erfc(abs(z) / math.sqrt(2))
    return ClusteredTest(
        kind="clustered_z",
        n=len(differences),
        clusters=count,
        mean_difference=float(mean),
        standard_error=se,
        z=z,
        p_value=p,
        significant=p is not None and p < float(ALPHA),
    )
