"""Read each alert window's actual materializations with its own pending state."""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import MetricFilters
from app.api.window import TimeWindow
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.error_queries import errors_by_hour, read_error_rollups
from app.rollups.filtered import FilteredHour, RollupFilters, read_filtered_rollups


async def read_metric_hours(
    db: AsyncSession,
    project_id: uuid.UUID,
    ranges: Sequence[tuple[datetime, datetime] | None],
    filters: MetricFilters,
    source_coverage: CoverageState,
) -> list[tuple[list[FilteredHour], CoverageState]]:
    """Deduplicate equal ranges; never let another output's pending work leak into this one."""
    cache: dict[tuple[datetime, datetime], tuple[list[FilteredHour], CoverageState]] = {}
    present = list(dict.fromkeys(bounds for bounds in ranges if bounds is not None))
    if len(present) > 1:
        # Retain 5A1's conservative source-loss hull while narrowing only pending dependencies.
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project_id,
            min(start for start, _ in present),
            max(end for _, end in present),
            source_coverage,
            relevant_consumers=0,
        )
    for bounds in present:
        state = CoverageState()
        hours = await _read_range(db, project_id, TimeWindow(*bounds), filters, state)
        source_coverage.unavailable |= state.unavailable
        cache[bounds] = hours, state
    return [([], CoverageState()) if bounds is None else cache[bounds] for bounds in ranges]


async def _read_range(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    filters: MetricFilters,
    coverage: CoverageState,
) -> list[FilteredHour]:
    rollup_filters = RollupFilters(filters.environment, filters.provider, filters.model)
    hours = await read_filtered_rollups(db, project_id, window, rollup_filters, coverage)
    if not filters.narrows_errors:
        return hours
    rows = await read_error_rollups(
        db, project_id, window, rollup_filters, provider_breakdown=False, coverage=coverage
    )
    counts = errors_by_hour(rows, filters.error_class, filters.rate_limit_kind)
    return [replace(hour, class_errors=counts.get(hour.bucket_start, 0)) for hour in hours]
