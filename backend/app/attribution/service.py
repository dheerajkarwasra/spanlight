"""The attribution read: which source answers, the page, the buckets and the totals.

A window of 24 hours or less is read live from the traces (`raw`): exact, any metadata key.
A longer window is read from the daily rollup rows (`rollup`): widened to whole UTC days,
promoted keys only, up to 15 minutes behind. The figures of the two paths agree on the days they
share; only the `other` fold exists on the rollup path.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.attribution import (
    AttributionBucketOut,
    AttributionOut,
    AttributionRowOut,
    AttributionSeriesOut,
    AttributionSource,
    AttributionTotalsOut,
    AttributionWindowOut,
    SeriesPointOut,
)
from app.api.window import TimeWindow
from app.attribution import queries
from app.attribution.aggregate_sql import GroupKind
from app.attribution.queries import AttributionSort
from app.core.errors import FieldError, ProblemError, key_not_promoted
from app.core.pagination import decode_value_cursor, encode_value_cursor
from app.end_users.days import ONE_DAY, day_bounds, midnight
from app.rollups.compute import HOUR, floor_hour

RAW_WINDOW = timedelta(hours=24)
ROLLUP_LAG_MINUTES = 15
SHARE_PLACES = 4


@dataclass(frozen=True)
class AttributionRequest:
    group_by: GroupKind
    key: str | None
    window: TimeWindow
    environment: str | None
    sort: AttributionSort
    limit: int
    cursor: str | None


def check_request(
    group_by: GroupKind, key: str | None, window: TimeWindow, promoted_keys: Sequence[str]
) -> AttributionSource:
    """Which source answers the request, after the key rule: a key is required with `metadata`
    and refused with any other kind (422), and over 24 hours it must be promoted
    (`KEY_NOT_PROMOTED`). The export checks the same way before it queues anything."""
    if group_by == "metadata" and key is None:
        message = "required when group_by is metadata"
    elif group_by != "metadata" and key is not None:
        message = f"only allowed when group_by is metadata, not {group_by}"
    else:
        if window.length <= RAW_WINDOW:
            return "raw"
        if group_by == "metadata" and key not in promoted_keys:
            raise key_not_promoted(key or "")
        return "rollup"
    raise ProblemError(
        422,
        "VALIDATION_ERROR",
        f"`key` is {message}.",
        errors=[FieldError(field="key", message=message)],
    )


def _money(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


def _count(value: Any) -> int:
    return 0 if value is None else int(value)


def _totals(row: Row[Any]) -> AttributionTotalsOut:
    return AttributionTotalsOut(
        traces=_count(row.traces),
        llm_calls=_count(row.llm_calls),
        errors=_count(row.errors),
        unpriced_calls=_count(row.unpriced_calls),
        input_tokens=_count(row.input_tokens),
        output_tokens=_count(row.output_tokens),
        cost_usd=_money(row.cost_usd),
        retries=None if row.retries is None else int(row.retries),
        retry_cost_usd=_money(row.retry_cost_usd),
    )


def _bucket(row: Row[Any] | None) -> AttributionBucketOut | None:
    if row is None or _count(row.traces) == 0:
        return None
    folded = None if row.folded_values is None else int(row.folded_values)
    return AttributionBucketOut(**_totals(row).model_dump(), folded_values=folded)


def _share(cost: Decimal | None, total: Decimal | None) -> float | None:
    if cost is None or total is None or total == 0:
        return None
    return round(float(cost / total), SHARE_PLACES)


def _item(row: Row[Any], total: Decimal | None) -> AttributionRowOut:
    totals = _totals(row)
    share = _share(totals.cost_usd, total)
    return AttributionRowOut(**totals.model_dump(), value=row.value, share_of_cost=share)


def _sort_value(item: AttributionRowOut, sort: AttributionSort) -> Decimal | int | None:
    if sort == "cost":
        return item.cost_usd
    if sort == "retry_cost":
        return item.retry_cost_usd
    return item.llm_calls if sort == "calls" else item.errors


def _page(
    rows: Sequence[Row[Any]], total: Decimal | None, request: AttributionRequest
) -> tuple[list[AttributionRowOut], str | None]:
    items = [_item(row, total) for row in rows[: request.limit]]
    if len(rows) <= request.limit:
        return items, None
    last = items[-1]
    return items, encode_value_cursor(_sort_value(last, request.sort), last.value)


def _by_bucket(rows: Sequence[Row[Any]]) -> dict[str | None, Row[Any]]:
    return {row.bucket: row for row in rows}


async def _read_raw(
    db: AsyncSession,
    project_id: uuid.UUID,
    request: AttributionRequest,
    after: queries.Cursor | None,
) -> tuple[list[Row[Any]], list[Row[Any]], AttributionWindowOut]:
    kind, key, env = request.group_by, request.key, request.environment
    rows = await queries.read_raw_groups(
        db, project_id, kind, key, request.window, env, request.sort, request.limit + 1, after
    )
    totals = await queries.read_totals(db, project_id, kind, key, env, window=request.window)
    window = AttributionWindowOut(from_=request.window.start, to=request.window.end)
    return rows, totals, window


async def _read_rollup(
    db: AsyncSession,
    project_id: uuid.UUID,
    request: AttributionRequest,
    after: queries.Cursor | None,
) -> tuple[list[Row[Any]], list[Row[Any]], AttributionWindowOut]:
    kind, key, env = request.group_by, request.key, request.environment
    days = day_bounds(request.window)
    rows = await queries.read_rollup_groups(
        db, project_id, kind, key, days, env, request.sort, request.limit + 1, after
    )
    totals = await queries.read_totals(db, project_id, kind, key, env, days=days)
    window = AttributionWindowOut(from_=midnight(days[0]), to=midnight(days[1]))
    return rows, totals, window


async def read_attribution(
    db: AsyncSession,
    project_id: uuid.UUID,
    promoted_keys: Sequence[str],
    request: AttributionRequest,
) -> AttributionOut:
    """The groups of the window, `sort` descending with unknowns last (keyset), with the
    buckets and the totals of the same source."""
    source = check_request(request.group_by, request.key, request.window, promoted_keys)
    after = decode_value_cursor(request.cursor) if request.cursor else None
    if source == "raw":
        rows, totals, window = await _read_raw(db, project_id, request, after)
    else:
        rows, totals, window = await _read_rollup(db, project_id, request, after)
    buckets = _by_bucket(totals)
    overall = _totals(buckets[None])
    items, next_cursor = _page(rows, overall.cost_usd, request)
    computed_at = buckets[None].computed_at
    pending = None
    if source == "rollup":
        days = day_bounds(request.window)
        kind, key, env = request.group_by, request.key, request.environment
        pending = await queries.count_pending(db, project_id, kind, key, days, env)
    return AttributionOut(
        group_by=request.group_by,
        key=request.key,
        window=window,
        source=source,
        updated_within_minutes=0 if source == "raw" else ROLLUP_LAG_MINUTES,
        computed_at=computed_at,
        pending_traces=pending,
        items=items,
        next_cursor=next_cursor,
        none=_bucket(buckets.get("none")),
        other=_bucket(buckets.get("other")),
        totals=overall,
    )


async def read_attribution_series(
    db: AsyncSession,
    project_id: uuid.UUID,
    promoted_keys: Sequence[str],
    kind: GroupKind,
    key: str | None,
    window: TimeWindow,
    env: str | None,
    value: str | None,
) -> AttributionSeriesOut:
    """The list's source and window, bucketed: per hour on `raw`, per UTC day on `rollup`,
    every bucket present (one without traces costs a known 0). `value` keeps that group only."""
    source = check_request(kind, key, window, promoted_keys)
    if source == "raw":
        rows = await queries.read_series(db, project_id, kind, key, env, value, window=window)
        start, end, step = window.start, window.end, HOUR
        current = floor_hour(start)
    else:
        days = day_bounds(window)
        rows = await queries.read_series(db, project_id, kind, key, env, value, days=days)
        start, end, step = midnight(days[0]), midnight(days[1]), ONE_DAY
        current = start
    stored = {_bucket_start(row.bucket_start): row for row in rows}
    points: list[SeriesPointOut] = []
    while current < end:
        row = stored.get(current)
        cost = Decimal(0) if row is None else _money(row.cost_usd)
        unpriced = 0 if row is None else _count(row.unpriced_calls)
        points.append(SeriesPointOut(bucket_start=current, cost_usd=cost, unpriced_calls=unpriced))
        current += step
    return AttributionSeriesOut(
        bucket="hour" if source == "raw" else "day",
        source=source,
        window=AttributionWindowOut(from_=start, to=end),
        points=points,
    )


def _bucket_start(value: date | datetime) -> datetime:
    return value if isinstance(value, datetime) else midnight(value)
