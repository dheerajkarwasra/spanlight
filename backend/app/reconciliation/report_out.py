"""The report's API shapes built from the arithmetic's results, and its summary. Pure: no I/O."""

from collections.abc import Iterable, Sequence
from decimal import Decimal

from app.api.schemas.reconciliation import (
    ExplainedLineOut,
    ListedChargeOut,
    ReportDayOut,
    ReportSummaryOut,
    ReportTokensOut,
)
from app.reconciliation.arithmetic import MATERIAL_PROVIDER_USD, ListedCharge, TokenTotals
from app.reconciliation.explained import ExplainedLine

# A settled, complete day whose variance is within this ratio counts towards the exit criterion.
WITHIN_RATIO = Decimal("0.02")


def summarize(days: Sequence[ReportDayOut]) -> ReportSummaryOut:
    """Counts over the days shown; the variance figures read only settled, complete days."""
    settled = [day for day in days if not day.provisional]
    complete = [day for day in settled if not day.incomplete]
    within = [
        day
        for day in complete
        if day.variance_ratio is not None and abs(day.variance_ratio) <= WITHIN_RATIO
    ]
    ratios = [
        abs(group.variance_ratio)
        for day in complete
        for group in day.groups
        if group.variance_ratio is not None
        and group.provider_usd is not None
        and group.provider_usd >= MATERIAL_PROVIDER_USD
    ]
    return ReportSummaryOut(
        days=len(days),
        settled_days=len(settled),
        days_within_2pct=len(within),
        max_abs_variance_ratio=max(ratios, default=None),
    )


def tokens_out(totals: TokenTotals | None) -> ReportTokensOut:
    """Token totals by billing class; every count null when the side is unknown."""
    if totals is None:
        return ReportTokensOut(
            uncached_input=None,
            cache_read=None,
            cache_write_5m=None,
            cache_write_1h=None,
            output=None,
        )
    return ReportTokensOut(
        uncached_input=totals.uncached_input,
        cache_read=totals.cache_read,
        cache_write_5m=totals.cache_write_5m,
        cache_write_1h=totals.cache_write_1h,
        output=totals.output,
    )


def explained_out(line: ExplainedLine) -> ExplainedLineOut:
    return ExplainedLineOut(
        kind=line.kind, usd=line.usd, count=line.count, tokens=line.tokens, note=line.note
    )


def charges_out(charges: Iterable[ListedCharge]) -> list[ListedChargeOut]:
    return [ListedChargeOut(description=charge.description, usd=charge.usd) for charge in charges]
