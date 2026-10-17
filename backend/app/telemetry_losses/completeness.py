"""The telemetry completeness ratio. Pure.

`stored / (stored + Σ losses)` over every source, to four decimal places rounded down, so any
loss keeps it below `1.0000` (a lower bound must never show 100 % while spans were lost);
`None` when nothing was stored and nothing lost (unknown, not 100 %), and exactly `0` when
nothing was stored but something was lost.

An SDK `failed` span may be one the server rejected, which ingest also counts, so when both are
non-zero the denominator can count a span twice and the ratio is a lower bound.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class CompletenessResult:
    completeness: Decimal | None
    lower_bound: bool


def ratio(stored: int, losses: Mapping[tuple[str, str], int]) -> CompletenessResult:
    """The ratio for `stored` spans and `losses` keyed by `(source, reason)`."""
    lost = sum(losses.values())
    sdk_failed = losses.get(("sdk", "failed"), 0) > 0
    ingest_rejected = any(n > 0 for (source, _), n in losses.items() if source == "ingest")
    lower_bound = sdk_failed and ingest_rejected
    if stored + lost == 0:
        return CompletenessResult(completeness=None, lower_bound=lower_bound)
    if stored == 0:
        return CompletenessResult(completeness=Decimal("0"), lower_bound=lower_bound)
    value = (Decimal(stored) / Decimal(stored + lost)).quantize(_PLACES, rounding=ROUND_DOWN)
    return CompletenessResult(completeness=value, lower_bound=lower_bound)
