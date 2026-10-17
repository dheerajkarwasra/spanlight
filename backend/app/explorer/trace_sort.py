"""Trace-list order and keyset pagination for each sort the list offers.

`started_at` (the default): newest first, ties by `trace_id` descending; the cursor is
`(started_at, trace_id)`.

`retry_cost_usd`: the largest retry cost first and unknown (NULL) costs last, then `started_at`
and `trace_id` descending; the cursor is `(retry_cost_usd, started_at, trace_id)`, its cost
`None` once the page reached the unknown costs.
"""

from typing import Any, Literal

from sqlalchemy import Select, and_, or_, tuple_
from sqlalchemy.sql.elements import ColumnElement

from app.core.pagination import decode_cursor, decode_sort_cursor, encode_cursor, encode_sort_cursor
from app.db.models import Trace

TraceSort = Literal["started_at", "retry_cost_usd"]


def ordering(sort: TraceSort, trace: type[Trace] = Trace) -> list[ColumnElement[Any]]:
    """The ORDER BY of `sort` over `trace` (the table, or an alias of a page of it)."""
    if sort == "retry_cost_usd":
        return [
            trace.retry_cost_usd.desc().nulls_last(),
            trace.started_at.desc(),
            trace.trace_id.desc(),
        ]
    return [trace.started_at.desc(), trace.trace_id.desc()]


def apply[*Ts](query: Select[*Ts], *, sort: TraceSort, cursor: str | None) -> Select[*Ts]:
    """`query` (over `Trace`) ordered by `sort`, starting after `cursor` when one is given."""
    query = query.order_by(*ordering(sort))
    if not cursor:
        return query
    if sort == "retry_cost_usd":
        return query.where(_after_retry_cost(cursor))
    started_at, trace_id = decode_cursor(cursor)
    return query.where(
        or_(
            Trace.started_at < started_at,
            and_(Trace.started_at == started_at, Trace.trace_id < trace_id),
        )
    )


def _after_retry_cost(cursor: str) -> ColumnElement[bool]:
    """The rows after the cursor's in `retry_cost_usd DESC NULLS LAST, started_at, trace_id`."""
    cost, started_at, trace_id = decode_sort_cursor(cursor)
    later_in_tie = tuple_(Trace.started_at, Trace.trace_id) < tuple_(started_at, trace_id)
    if cost is None:
        return and_(Trace.retry_cost_usd.is_(None), later_in_tie)
    return or_(
        Trace.retry_cost_usd < cost,
        and_(Trace.retry_cost_usd == cost, later_in_tie),
        Trace.retry_cost_usd.is_(None),
    )


def next_cursor(trace: Trace, sort: TraceSort) -> str:
    """The cursor that continues a page whose last row is `trace`."""
    if sort == "retry_cost_usd":
        return encode_sort_cursor(trace.retry_cost_usd, trace.started_at, trace.trace_id)
    return encode_cursor(trace.started_at, trace.trace_id)
