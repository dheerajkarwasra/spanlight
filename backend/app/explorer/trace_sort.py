"""Trace-list order and keyset pagination for each sort the list offers.

Every sort takes a direction (`desc`, the default, or `asc`).

`started_at` (the default): newest first (`asc`: oldest first), ties by `trace_id` in the same
direction; the cursor is `(started_at, trace_id)`.

The value sorts, `duration` (`ended_at - started_at`), `cost` (`cost_usd`), `tokens`
(`input_tokens + output_tokens`) and `retry_cost_usd`: the value in the sort's direction with
unknown (NULL) values last in both directions, then `started_at` and `trace_id` in the same
direction; the cursor is `(value, started_at, trace_id)` (a duration in milliseconds), its value
`None` once the page reached the unknown values. The direction is not in the cursor: the request
repeats it.
"""

import operator
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, and_, or_, tuple_
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

from app.core.pagination import decode_cursor, decode_sort_cursor, encode_cursor, encode_sort_cursor
from app.db.models import Trace

TraceSort = Literal["started_at", "duration", "cost", "tokens", "retry_cost_usd"]
SortOrder = Literal["asc", "desc"]

_MICROSECOND = timedelta(microseconds=1)
# The durations a cursor can name; no stored trace comes near either end, so clamping a forged
# cursor to them leaves the page where it was.
_MIN_DURATION_MS = Decimal(timedelta.min // _MICROSECOND) / 1000
_MAX_DURATION_MS = Decimal(timedelta.max // _MICROSECOND) / 1000


def _value(sort: TraceSort) -> ColumnElement[Any] | InstrumentedAttribute[Any]:
    """The sorted value of a value sort, as SQL."""
    if sort == "duration":
        # The interval itself: ordering by it is cheaper than by its milliseconds (measured).
        return Trace.ended_at - Trace.started_at
    if sort == "cost":
        return Trace.cost_usd
    if sort == "tokens":
        return Trace.input_tokens + Trace.output_tokens
    return Trace.retry_cost_usd


def _value_of(trace: Trace, sort: TraceSort) -> Decimal | None:
    """`trace`'s value of a value sort, as `_value` computes it."""
    if sort == "duration":
        return Decimal((trace.ended_at - trace.started_at) // _MICROSECOND) / 1000
    if sort == "cost":
        return trace.cost_usd
    if sort == "tokens":
        return Decimal(trace.input_tokens + trace.output_tokens)
    return trace.retry_cost_usd


def ordering(sort: TraceSort, order: SortOrder) -> list[ColumnElement[Any]]:
    """The ORDER BY of `sort` in direction `order`."""
    keys: list[ColumnElement[Any]] = [_in(Trace.started_at, order), _in(Trace.trace_id, order)]
    if sort == "started_at":
        return keys
    return [_in(_value(sort), order).nulls_last(), *keys]


def _in(
    column: ColumnElement[Any] | InstrumentedAttribute[Any], order: SortOrder
) -> UnaryExpression[Any]:
    return column.asc() if order == "asc" else column.desc()


def apply[*Ts](
    query: Select[*Ts], *, sort: TraceSort, order: SortOrder, cursor: str | None
) -> Select[*Ts]:
    """`query` (over `Trace`) ordered by `sort` and `order`, starting after `cursor` if given."""
    query = query.order_by(*ordering(sort, order))
    if not cursor:
        return query
    if sort != "started_at":
        return query.where(_after_value(sort, order, cursor))
    started_at, trace_id = decode_cursor(cursor)
    after = operator.gt if order == "asc" else operator.lt
    return query.where(
        or_(
            after(Trace.started_at, started_at),
            and_(Trace.started_at == started_at, after(Trace.trace_id, trace_id)),
        )
    )


def _after_value(sort: TraceSort, order: SortOrder, cursor: str) -> ColumnElement[bool]:
    """The rows after the cursor's in `value NULLS LAST, started_at, trace_id`, all in `order`."""
    cursor_value, started_at, trace_id = decode_sort_cursor(cursor)
    value = _value(sort)
    bound: Decimal | timedelta | None = cursor_value
    if sort == "duration" and cursor_value is not None:
        clamped = max(_MIN_DURATION_MS, min(cursor_value, _MAX_DURATION_MS))
        bound = timedelta(microseconds=int(clamped * 1000))
    after = operator.gt if order == "asc" else operator.lt
    later_in_tie = after(tuple_(Trace.started_at, Trace.trace_id), tuple_(started_at, trace_id))
    if bound is None:
        return and_(value.is_(None), later_in_tie)
    return or_(
        after(value, bound),
        and_(value == bound, later_in_tie),
        value.is_(None),
    )


def next_cursor(trace: Trace, sort: TraceSort) -> str:
    """The cursor that continues a page whose last row is `trace` (in either direction)."""
    if sort != "started_at":
        return encode_sort_cursor(_value_of(trace, sort), trace.started_at, trace.trace_id)
    return encode_cursor(trace.started_at, trace.trace_id)
