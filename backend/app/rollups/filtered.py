"""Read the hourly span rollups with dimension filters, for alert rules and budgets.

``read_rollups`` (``app.rollups.queries``) serves the metrics endpoints, which only filter by
environment and never need time to first token. Alert metrics also filter by provider and model
and estimate a TTFT percentile, so they read through :func:`read_filtered_rollups` instead. The
definitions are the same as there:

* ``llm_calls``, ``llm_errors``, ``unpriced`` and both histograms come from rows of kind ``llm``.
* ``spans``, ``cost_usd`` and token totals come from rows of every kind. A cost is ``None`` when
  every contributing row is unpriced, never ``0``; ``spans`` tells "no spans" from "unpriced".
* Each filter matches its dimension exactly (``model`` also matches a snapshot of the name, see
  ``app.core.model_match``), so rows with an unknown (NULL) value only appear
  when that filter is not given. ``environment`` is the trace's environment.

The window covers the hours whose ``bucket_start`` lies in ``[floor_hour(start), end)``, so a
window edge inside an hour reads that hour in full.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.core.model_match import like_prefix, model_filter_sql
from app.rollups.compute import floor_hour
from app.rollups.queries import summed_histogram

# A NULL parameter means "any value" for that dimension.
_FILTERED_HOURS = text(
    f"""
    SELECT bucket_start,
           sum(span_count)                                               AS spans,
           coalesce(sum(span_count) FILTER (WHERE kind = 'llm'), 0)      AS llm_calls,
           coalesce(sum(errors) FILTER (WHERE kind = 'llm'), 0)          AS llm_errors,
           coalesce(sum(unpriced_calls) FILTER (WHERE kind = 'llm'), 0)  AS unpriced,
           sum(cost_usd)                                                 AS cost_usd,
           coalesce(sum(input_tokens), 0)                                AS input_tokens,
           coalesce(sum(output_tokens), 0)                               AS output_tokens,
           {summed_histogram("latency_buckets")}                        AS latency_buckets,
           {summed_histogram("ttft_buckets")}                           AS ttft_buckets
    FROM span_rollups_hourly
    WHERE project_id = :project_id
      AND bucket_start >= :start
      AND bucket_start < :end
      AND (:environment IS NULL OR environment = :environment)
      AND (:provider IS NULL OR provider = :provider)
      AND (:model IS NULL OR {model_filter_sql("model")})
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
).bindparams(
    bindparam("environment", type_=String),
    bindparam("provider", type_=String),
    bindparam("model", type_=String),
    bindparam("model_like", type_=String),
)


@dataclass(frozen=True, slots=True)
class RollupFilters:
    """Dimension filters; ``None`` means any value. ``model`` also matches its snapshots."""

    environment: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class FilteredHour:
    """The filtered span metrics of one UTC hour.

    ``class_errors`` is set by a caller that also reads the error rollups for an error class or
    rate-limit kind (``app.alerts.metrics``): that hour's failed llm calls of the class and kind.
    It is ``None`` when nobody read them.
    """

    bucket_start: datetime
    spans: int
    llm_calls: int
    llm_errors: int
    unpriced: int
    cost_usd: Decimal | None
    input_tokens: int
    output_tokens: int
    latency_buckets: list[int]
    ttft_buckets: list[int]
    class_errors: int | None = None

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


async def read_filtered_rollups(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, filters: RollupFilters
) -> list[FilteredHour]:
    """One row per hour that has matching rollup rows, oldest first. Empty hours are absent."""
    params = {
        "project_id": project_id,
        "start": floor_hour(window.start),
        "end": window.end,
        "environment": filters.environment,
        "provider": filters.provider,
        "model": filters.model,
        "model_like": None if filters.model is None else like_prefix(filters.model),
    }
    rows = (await db.execute(_FILTERED_HOURS, params)).all()
    return [
        FilteredHour(
            bucket_start=row.bucket_start,
            spans=int(row.spans),
            llm_calls=int(row.llm_calls),
            llm_errors=int(row.llm_errors),
            unpriced=int(row.unpriced),
            cost_usd=row.cost_usd,
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            latency_buckets=[int(count) for count in row.latency_buckets],
            ttft_buckets=[int(count) for count in row.ttft_buckets],
        )
        for row in rows
    ]
