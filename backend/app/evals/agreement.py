"""Agreement between human reviewers and an LLM judge on one score name. Pure: no database, no I/O.

Numeric scores are clamped to the score config's `[min_value, max_value]` (`[0, 1]` when either is
unset), put in `NUMERIC_BINS` equal-width bins and compared with quadratic-weighted kappa; boolean
scores (stored 1/0) use a 2x2 table and Cohen's kappa. F1 treats "pass" (a value at or above the
scale midpoint, or Yes) as the positive class with the human score as the reference; matrix rows are
the reviewer, columns the judge. Arithmetic is exact in `Fraction`; kappa and F1 are quantized to 4
places, `ROUND_HALF_EVEN`, as in `evals/stats.py`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Literal

from app.evals.stats import PLACES, quantize_rate
from app.scores.types import ScoreDataType

MIN_PAIRS = 30
KAPPA_FLAG = Decimal("0.60")
MAX_PAIRS = 2000
NUMERIC_BINS = 5

AgreementStatus = Literal["ok", "not_enough_pairs", "undefined", "unsupported"]
KappaKind = Literal["quadratic_weighted", "cohen"]

_BOOLEAN_LABELS = ("No", "Yes")


@dataclass(frozen=True, slots=True)
class Agreement:
    status: AgreementStatus
    pairs: int
    kappa: Decimal | None
    kappa_kind: KappaKind | None
    f1: Decimal | None
    pass_threshold: Decimal | None
    labels: tuple[str, ...]
    matrix: tuple[tuple[int, ...], ...]
    flagged: bool


def _edge(value: Decimal) -> str:
    """A bin edge without trailing zeros: `0.20` -> `0.2`, `10.0` -> `10`."""
    return format(value.normalize(), "f")


def _weighted_kappa(matrix: list[list[int]], n: int) -> Fraction | None:
    """Quadratic-weighted kappa, `1 - sum(w*O) / sum(w*E)`; `None` when `sum(w*E)` is 0 (every
    pair in one class). For two classes this equals Cohen's `(po - pe) / (1 - pe)`."""
    k = len(matrix)
    rows = [sum(row) for row in matrix]
    cols = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    weight = [[Fraction((i - j) ** 2, (k - 1) ** 2) for j in range(k)] for i in range(k)]
    observed = sum(weight[i][j] * matrix[i][j] for i in range(k) for j in range(k))
    expected = sum(
        weight[i][j] * Fraction(rows[i] * cols[j], n) for i in range(k) for j in range(k)
    )
    return None if expected == 0 else 1 - observed / expected


def agreement(
    pairs: Sequence[tuple[Decimal, Decimal]],
    *,
    data_type: ScoreDataType | None,
    min_value: Decimal | None,
    max_value: Decimal | None,
) -> Agreement:
    """Agreement over `(human, judge)` pairs. A missing score config (`data_type` `None`) is read as
    numeric, since a judge verdict is a number in `[0, 1]`."""
    boolean = data_type is ScoreDataType.BOOLEAN
    if boolean:
        lo, hi = Decimal(0), Decimal(1)
    else:
        lo, hi = (
            (Decimal(0), Decimal(1))
            if min_value is None or max_value is None
            else (min_value, max_value)
        )
    if data_type is ScoreDataType.CATEGORICAL or hi <= lo:
        return Agreement("unsupported", len(pairs), None, None, None, None, (), (), False)

    k = 2 if boolean else NUMERIC_BINS
    width = hi - lo
    threshold = (lo + hi) / 2
    if boolean:
        labels: tuple[str, ...] = _BOOLEAN_LABELS
    else:
        labels = tuple(
            f"{_edge(lo + width * i / k)}–{_edge(lo + width * (i + 1) / k)}"  # noqa: RUF001
            for i in range(k)
        )

    matrix = [[0] * k for _ in range(k)]
    tp = fp = fn = 0
    for human, judge in pairs:
        h, j = (min(hi, max(lo, v)) for v in (human, judge))
        # Boolean 1/0 lands in bin 1/0, so one rule bins both data types.
        matrix[min(int(Fraction(h - lo) / Fraction(width) * k), k - 1)][
            min(int(Fraction(j - lo) / Fraction(width) * k), k - 1)
        ] += 1
        h_pass, j_pass = h >= threshold, j >= threshold
        tp += h_pass and j_pass
        fp += j_pass and not h_pass
        fn += h_pass and not j_pass

    n = len(pairs)
    kind: KappaKind = "cohen" if boolean else "quadratic_weighted"
    shown_matrix = tuple(tuple(row) for row in matrix) if n else ()
    pass_threshold = None if boolean else threshold
    if n < MIN_PAIRS:
        return Agreement(
            "not_enough_pairs", n, None, kind, None, pass_threshold, labels, shown_matrix, False
        )

    f1_den = 2 * tp + fp + fn
    f1 = quantize_rate(Fraction(2 * tp, f1_den)) if f1_den else None
    exact = _weighted_kappa(matrix, n)
    if exact is None:
        return Agreement(
            "undefined", n, None, kind, f1, pass_threshold, labels, shown_matrix, False
        )
    kappa = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
        PLACES, rounding=ROUND_HALF_EVEN
    )
    # Flag on the value shown, so a displayed 0.6000 is never called weak.
    return Agreement(
        "ok", n, kappa, kind, f1, pass_threshold, labels, shown_matrix, kappa < KAPPA_FLAG
    )
