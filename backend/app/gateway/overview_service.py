"""The gateway overview: counts and latencies of a window's gateway calls, with their ratios.

A ratio is `None` when its denominator is zero, never 0; a latency is `None` when no request
measured it (`app.gateway.overview_queries` says where each number comes from).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.gateway import (
    FaultScenarioCountOut,
    GatewayCacheOut,
    GatewayKeyUsageOut,
    GatewayOverviewOut,
    GatewaySeriesOut,
    GatewaySeriesPointOut,
    GatewayTargetUsageOut,
    GatewayTotalsOut,
)
from app.api.window import TimeWindow
from app.gateway import overview_queries as queries

_BUCKET_ORIGIN = datetime(2001, 1, 1, tzinfo=UTC)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _previous(totals: Row[Any]) -> GatewayTotalsOut:
    return GatewayTotalsOut(
        requests=totals.requests,
        errors=totals.errors,
        error_rate=ratio(totals.errors, totals.requests),
        p95_overhead_ms=totals.p95_overhead_ms,
        cache_hit_rate=ratio(totals.cache_hits, totals.cache_hits + totals.cache_misses),
        fallbacks=totals.fallbacks,
        rate_limited=totals.rate_limited,
    )


async def _series(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> GatewaySeriesOut:
    """Every clock-aligned bucket the window touches, at most 169 (7 days of hours, plus one)."""
    five_minutes = window.length <= timedelta(hours=1)
    step = timedelta(minutes=5) if five_minutes else timedelta(hours=1)
    rows = await queries.read_series(db, project_id, window, environment, step)
    counts = {row.bucket_start: (row.requests, row.errors) for row in rows}
    points: list[GatewaySeriesPointOut] = []
    # The same origin as the statement's `date_bin`, so the gap-fill meets its bucket starts.
    current = window.start - (window.start - _BUCKET_ORIGIN) % step
    while current < window.end:
        requests, errors = counts.get(current, (0, 0))
        points.append(GatewaySeriesPointOut(bucket_start=current, requests=requests, errors=errors))
        current += step
    return GatewaySeriesOut(bucket="5m" if five_minutes else "hour", points=points)


async def overview(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
) -> GatewayOverviewOut:
    """Six statements, each bounded by a window and the project's gateway spans."""
    totals = await queries.read_totals(db, project_id, window, environment)
    previous = await queries.read_totals(db, project_id, window.previous(), environment)
    series = await _series(db, project_id, window, environment)
    by_target = await queries.read_by_target(db, project_id, org_id, window, environment)
    by_key = await queries.read_by_key(db, project_id, window, environment)
    faults = await queries.read_faults(db, project_id, window, environment)
    return GatewayOverviewOut(
        requests=totals.requests,
        errors=totals.errors,
        error_rate=ratio(totals.errors, totals.requests),
        cache=GatewayCacheOut(
            hits=totals.cache_hits,
            misses=totals.cache_misses,
            hit_rate=ratio(totals.cache_hits, totals.cache_hits + totals.cache_misses),
        ),
        fallbacks=totals.fallbacks,
        retries=totals.retries,
        p95_overhead_ms=totals.p95_overhead_ms,
        p95_ttft_ms=totals.p95_ttft_ms,
        p95_upstream_ms=totals.p95_upstream_ms,
        rate_limited=totals.rate_limited,
        previous=_previous(previous),
        series=series,
        # Both statements name their columns after the response fields.
        by_target=[GatewayTargetUsageOut(**row._mapping) for row in by_target],
        by_key=[GatewayKeyUsageOut(**row._mapping) for row in by_key],
        faults=[FaultScenarioCountOut(scenario=row.scenario, count=row.calls) for row in faults],
    )
