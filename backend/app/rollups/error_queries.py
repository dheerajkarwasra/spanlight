"""Read the hourly error rollups, for provider health and the error-class alert metrics.

``error_rollups_hourly`` (written by ``app.rollups.error_compute``) counts failed ``llm`` spans per
hour by environment, provider, model, error class and rate-limit kind. :func:`read_error_rollups`
sums those rows per hour, error class and rate-limit kind, and also per provider when asked to.

The filters follow ``app.rollups.filtered``: each one matches its dimension exactly (``model``
also matches a snapshot of the name), so rows with an unknown (NULL) value only appear when that
filter is not given. The window covers the hours whose ``bucket_start`` lies in
``[floor_hour(start), end)``, so a window edge inside an hour reads that hour in full. An hour
with no failures has no row; a caller that needs zeros fills them in. :func:`errors_by_hour`
sums the rows of one error class and rate-limit kind per hour, for the alert metrics.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.core.model_match import like_prefix, model_filter_sql
from app.rollups.compute import floor_hour
from app.rollups.filtered import RollupFilters

# A NULL filter parameter means "any value". Without the breakdown every provider's rows are
# summed into one group whose provider is NULL.
_ERROR_HOURS = text(
    f"""
    SELECT bucket_start,
           CASE WHEN :provider_breakdown THEN provider END  AS provider,
           error_class,
           rate_limit_kind,
           sum(errors)                                      AS errors,
           sum(with_retry_after)                            AS with_retry_after
    FROM error_rollups_hourly
    WHERE project_id = :project_id
      AND bucket_start >= :start
      AND bucket_start < :end
      AND (:environment IS NULL OR environment = :environment)
      AND (:provider IS NULL OR provider = :provider)
      AND (:model IS NULL OR {model_filter_sql("model")})
    GROUP BY 1, 2, 3, 4
    ORDER BY 1, 2 NULLS LAST, 3, 4 NULLS LAST
    """
).bindparams(
    bindparam("provider_breakdown", type_=Boolean),
    bindparam("environment", type_=String),
    bindparam("provider", type_=String),
    bindparam("model", type_=String),
    bindparam("model_like", type_=String),
)


@dataclass(frozen=True, slots=True)
class ErrorHour:
    """Failed llm calls of one UTC hour, error class and rate-limit kind.

    ``provider`` is the provider when the read was broken down by provider (``None`` for calls
    whose provider is unknown), and always ``None`` otherwise. ``rate_limit_kind`` is ``None``
    for failures that are not capacity refusals. ``with_retry_after`` is the subset of
    ``errors`` that carried a provider ``retry-after``.
    """

    bucket_start: datetime
    provider: str | None
    error_class: str
    rate_limit_kind: str | None
    errors: int
    with_retry_after: int


async def read_error_rollups(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    filters: RollupFilters,
    *,
    provider_breakdown: bool,
) -> list[ErrorHour]:
    """One row per hour, [provider,] error class and rate-limit kind that had failures, oldest
    hour first."""
    params = {
        "project_id": project_id,
        "start": floor_hour(window.start),
        "end": window.end,
        "provider_breakdown": provider_breakdown,
        "environment": filters.environment,
        "provider": filters.provider,
        "model": filters.model,
        "model_like": None if filters.model is None else like_prefix(filters.model),
    }
    rows = (await db.execute(_ERROR_HOURS, params)).all()
    return [
        ErrorHour(
            bucket_start=row.bucket_start,
            provider=row.provider,
            error_class=row.error_class,
            rate_limit_kind=row.rate_limit_kind,
            errors=int(row.errors),
            with_retry_after=int(row.with_retry_after),
        )
        for row in rows
    ]


def errors_by_hour(
    rows: Iterable[ErrorHour], error_class: str | None, rate_limit_kind: str | None
) -> dict[datetime, int]:
    """The failed calls of each hour whose class and kind match; ``None`` matches any value."""
    counts: dict[datetime, int] = {}
    for row in rows:
        if error_class not in (None, row.error_class):
            continue
        if rate_limit_kind not in (None, row.rate_limit_kind):
            continue
        counts[row.bucket_start] = counts.get(row.bucket_start, 0) + row.errors
    return counts
