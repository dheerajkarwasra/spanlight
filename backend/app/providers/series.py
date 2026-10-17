"""Fold the rolled-up and raw error counts into what the provider health routes answer. Pure.

The counts come in two halves (``app.alerts.metrics.plan_window``): whole hours before the split
from the hourly rollups, the rest from raw spans. Both halves count the same spans
(``app.rollups.error_compute.FAILED_LLM_SPAN``), so adding them is exact. A bucket that holds a
rollup hour is ``approximate``: its counts are as of the rollup job's last run over that hour, so
a span ingested late for that hour shows once the job recomputes it.

Classes are listed in ``ERROR_CLASS_ORDER`` (the page's fixed stacking order) and rate-limit kinds
in ``RATE_LIMIT_KIND_ORDER``; a class or kind with no failures is left out, never listed as 0.
"""

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from app.api.window import TimeWindow
from app.ingest.error_class import ErrorClass
from app.ingest.rate_limit_kind import RateLimitKind
from app.rollups.compute import HOUR, floor_hour

Bucket = Literal["hour", "day"]

ERROR_CLASS_ORDER: tuple[str, ...] = (
    ErrorClass.RATE_LIMIT,
    ErrorClass.PROVIDER_5XX,
    ErrorClass.TIMEOUT,
    ErrorClass.NETWORK,
    ErrorClass.AUTH,
    ErrorClass.CONTEXT_LENGTH,
    ErrorClass.CONTENT_FILTER,
    ErrorClass.CLIENT,
    ErrorClass.GATEWAY,
    ErrorClass.UNKNOWN,
)
"""Every error class, in the order the page stacks and lists them."""

RATE_LIMIT_KIND_ORDER: tuple[str, ...] = tuple(RateLimitKind)
"""Every rate-limit kind, in the order the 429 kinds table lists them."""

_RATE_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class ErrorCount:
    """Failed llm calls of one provider, error class and rate-limit kind."""

    provider: str | None
    error_class: str
    rate_limit_kind: str | None
    errors: int
    with_retry_after: int


@dataclass(frozen=True, slots=True)
class KindCount:
    kind: str
    count: int
    with_retry_after: int


@dataclass(slots=True)
class ProviderCounts:
    """One provider's calls and failures over a window."""

    provider: str | None
    llm_calls: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    kinds: dict[str, KindCount] = field(default_factory=dict)

    @property
    def errors(self) -> int:
        return sum(self.by_class.values())

    def classes(self) -> list[tuple[str, int]]:
        """The non-zero classes in page order."""
        return [(name, self.by_class[name]) for name in _ordered(self.by_class, ERROR_CLASS_ORDER)]

    def rate_limit_kinds(self) -> list[KindCount]:
        """The non-zero kinds in table order."""
        return [self.kinds[name] for name in _ordered(self.kinds, RATE_LIMIT_KIND_ORDER)]


@dataclass(frozen=True, slots=True)
class ErrorBucket:
    bucket_start: datetime
    by_class: dict[str, int]
    approximate: bool


def error_rate(errors: int, llm_calls: int) -> Decimal | None:
    """``errors / llm_calls`` to six places; ``None`` (unknown) without calls, never 0."""
    if llm_calls == 0:
        return None
    return (Decimal(errors) / Decimal(llm_calls)).quantize(_RATE_PLACES, ROUND_HALF_EVEN)


def decimal_text(value: Decimal) -> str:
    """The shortest plain form: ``0.6``, ``0``, ``1``; never an exponent."""
    return format(value.normalize(), "f")


def provider_counts(
    calls: Iterable[tuple[str | None, int]], errors: Iterable[ErrorCount]
) -> dict[str | None, ProviderCounts]:
    """Add up calls and failures per provider (each input may repeat a provider)."""
    totals: dict[str | None, ProviderCounts] = {}

    def of(provider: str | None) -> ProviderCounts:
        return totals.setdefault(provider, ProviderCounts(provider))

    for provider, count in calls:
        of(provider).llm_calls += count
    for row in errors:
        counts = of(row.provider)
        counts.by_class[row.error_class] = counts.by_class.get(row.error_class, 0) + row.errors
        if row.rate_limit_kind is None:
            continue
        kind = counts.kinds.get(row.rate_limit_kind, KindCount(row.rate_limit_kind, 0, 0))
        counts.kinds[row.rate_limit_kind] = KindCount(
            kind.kind, kind.count + row.errors, kind.with_retry_after + row.with_retry_after
        )
    return totals


def busiest_first(
    providers: Iterable[str | None], counts: Mapping[str | None, ProviderCounts]
) -> list[str | None]:
    """Providers by calls, then failures, descending; then by name, the unknown one last."""
    return sorted(
        providers,
        key=lambda p: (
            -_get(counts, p).llm_calls,
            -_get(counts, p).errors,
            p is None,
            p or "",
        ),
    )


def _get(counts: Mapping[str | None, ProviderCounts], provider: str | None) -> ProviderCounts:
    return counts.get(provider) or ProviderCounts(provider)


def bucket_series(
    window: TimeWindow,
    bucket: Bucket,
    hours: Mapping[tuple[datetime, str], int],
    *,
    rollup_hours: Collection[datetime],
) -> list[ErrorBucket]:
    """Gapless buckets from the one holding ``window.start`` up to ``window.end``.

    ``hours`` maps ``(hour, error_class)`` to failed calls; ``rollup_hours`` are the hours read
    from the rollup, which make their bucket ``approximate``.
    """
    by_bucket: dict[datetime, dict[str, int]] = defaultdict(dict)
    for (hour, error_class), count in hours.items():
        if count:
            classes = by_bucket[bucket_start(hour, bucket)]
            classes[error_class] = classes.get(error_class, 0) + count
    approximate = {bucket_start(hour, bucket) for hour in rollup_hours}
    step = HOUR if bucket == "hour" else timedelta(days=1)
    buckets: list[ErrorBucket] = []
    current = bucket_start(floor_hour(window.start), bucket)
    while current < window.end:
        classes = by_bucket.get(current, {})
        ordered = {name: classes[name] for name in _ordered(classes, ERROR_CLASS_ORDER)}
        buckets.append(ErrorBucket(current, ordered, current in approximate))
        current += step
    return buckets


def bucket_start(hour: datetime, bucket: Bucket) -> datetime:
    """The UTC bucket that holds an hour: the hour itself, or midnight of its day."""
    utc_hour = hour.astimezone(UTC)
    return utc_hour if bucket == "hour" else utc_hour.replace(hour=0)


def hours_between(start: datetime, end: datetime) -> list[datetime]:
    """The whole hours starting in ``[start, end)``; both are hour-aligned."""
    hours, current = [], start
    while current < end:
        hours.append(current)
        current += HOUR
    return hours


def _ordered(present: Collection[str], order: tuple[str, ...]) -> list[str]:
    """``present``'s names with a non-empty entry, in ``order``; any name outside it last."""
    known = [name for name in order if name in present]
    return known + sorted(name for name in present if name not in order)
