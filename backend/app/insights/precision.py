"""Doctor precision from user feedback: right/wrong votes per detector kind, a Wilson 95 % interval
on the proportion, and whether the kind meets the target.

Pure: the caller counts the votes; this module only does the arithmetic. Everything is computed in
`Decimal`, so the published values are exact and reproducible, then rounded half-up to 4 places
for display. `meets_target` compares the unrounded values so nobody rounds across the line.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

PRECISION_TARGET = Decimal("0.8")
WILSON_LOW_TARGET = Decimal("0.6")
MIN_FEEDBACK = 20

PROVIDER_PATH_KINDS: frozenset[str] = frozenset(
    {
        "error_spike",
        "retry_storm",
        "retry_after_ignored",
        "rate_limit_pressure",
        "provider_incident",
        "client_timeout_misconfigured",
        "truncated_stream_accepted",
        "unsupported_parameter_retried",
    }
)

# The normal quantile of a two-sided 95 % interval.
_Z95 = 1.96
_PLACES = Decimal("0.0001")
_PRECISION_DIGITS = 50
_ZERO = Decimal(0)
_ONE = Decimal(1)


def _round(value: Decimal) -> Decimal:
    return value.quantize(_PLACES, rounding=ROUND_HALF_UP)


def _wilson_exact(successes: int, total: int, z: float) -> tuple[Decimal, Decimal] | None:
    """The unrounded Wilson score interval, clamped to [0, 1]; None when there is no sample."""
    if not 0 <= successes <= total:
        raise ValueError(f"successes must be within [0, total], got {successes} of {total}")
    if total == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = _PRECISION_DIGITS
        z_d = Decimal(str(z))
        n = Decimal(total)
        p = Decimal(successes) / n
        z2 = z_d * z_d
        denominator = 1 + z2 / n
        centre = (p + z2 / (2 * n)) / denominator
        half = z_d * (p * (1 - p) / n + z2 / (4 * n * n)).sqrt() / denominator
        return max(_ZERO, centre - half), min(_ONE, centre + half)


def wilson(successes: int, total: int, z: float = _Z95) -> tuple[Decimal, Decimal] | None:
    """Wilson score interval for a proportion, rounded half-up to 4 places; None without a
    sample. `ValueError` unless 0 <= successes <= total."""
    bounds = _wilson_exact(successes, total, z)
    if bounds is None:
        return None
    return _round(bounds[0]), _round(bounds[1])


@dataclass(frozen=True, slots=True)
class PrecisionRow:
    """Feedback counts for one detector kind. Every derived value is None when `n` is 0."""

    kind: str
    right: int
    wrong: int

    def __post_init__(self) -> None:
        if self.right < 0 or self.wrong < 0:
            raise ValueError(f"vote counts cannot be negative, got {self.right} and {self.wrong}")

    @property
    def n(self) -> int:
        return self.right + self.wrong

    def _exact_precision(self) -> Decimal | None:
        if self.n == 0:
            return None
        with localcontext() as ctx:
            ctx.prec = _PRECISION_DIGITS
            return Decimal(self.right) / Decimal(self.n)

    @property
    def precision(self) -> Decimal | None:
        exact = self._exact_precision()
        return None if exact is None else _round(exact)

    @property
    def wilson_low(self) -> Decimal | None:
        bounds = wilson(self.right, self.n)
        return None if bounds is None else bounds[0]

    @property
    def wilson_high(self) -> Decimal | None:
        bounds = wilson(self.right, self.n)
        return None if bounds is None else bounds[1]

    @property
    def meets_target(self) -> bool:
        """Precision ≥ 0.8, Wilson lower bound ≥ 0.6 and n ≥ 20, on the unrounded values."""
        precision = self._exact_precision()
        bounds = _wilson_exact(self.right, self.n, _Z95)
        if precision is None or bounds is None or self.n < MIN_FEEDBACK:
            return False
        return precision >= PRECISION_TARGET and bounds[0] >= WILSON_LOW_TARGET
