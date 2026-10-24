"""SQL for the sessions list and the session turns (`app.api.v1.sessions`).

Every statement filters on `project_id` explicitly as well as through row-level security, which
keeps index usage obvious. A session's row sums its traces started in the window (and in the
environment, when one is given); the search and the cursor apply after the grouping.

Sorts: `last` (the default) by the latest trace end, ties by `session_id`, both in the request's
direction, cursor `(last_at, session_id)`; the value sorts `cost`, `turns`, `errors` and `tokens`
by the value in the request's direction with unknown (NULL) values last in both directions, ties
by `session_id` in the same direction, cursor `(value, session_id)` with value `None` once the
page reached the unknown values. The direction is not in the cursor: the request repeats it.
"""

import operator
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    ARRAY,
    Numeric,
    Row,
    String,
    Text,
    and_,
    bindparam,
    case,
    cast,
    distinct,
    func,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.pagination import (
    decode_cursor,
    decode_value_cursor,
    encode_cursor,
    encode_value_cursor,
)
from app.db.models import Trace
from app.explorer.trace_queries import escape_like
from app.explorer.trace_sort import TOKENS, SortOrder

SessionSort = Literal["last", "cost", "turns", "errors", "tokens"]

# Fewer sessions with a cost than this and the 99th percentile is one session's cost or close to it.
P99_MIN_SESSIONS = 20

_LAST_AT = func.max(Trace.ended_at)
_VALUES: dict[SessionSort, ColumnElement[Any]] = {
    "cost": func.sum(Trace.cost_usd),
    "turns": func.count(),
    "errors": func.sum(Trace.error_count),
    "tokens": func.sum(TOKENS),
}
# Only aggregates Postgres can hash: a distinct count or an ordered aggregate here made it sort
# the window's traces on disk, twice the time over 30 days (measured). Those come per page.
_COLUMNS = (
    Trace.session_id,
    func.count().label("trace_count"),
    func.min(Trace.started_at).label("first_at"),
    _LAST_AT.label("last_at"),
    _VALUES["cost"].label("cost_usd"),
    func.bool_or(Trace.has_unpriced).label("has_unpriced"),
    func.sum(Trace.error_count).label("error_count"),
    # Traces without a user id do not disagree with the others.
    case(
        (
            func.min(Trace.external_user_id) == func.max(Trace.external_user_id),
            func.min(Trace.external_user_id),
        )
    ).label("external_user_id"),
    func.sum(Trace.input_tokens).label("input_tokens"),
    func.sum(Trace.output_tokens).label("output_tokens"),
    (func.extract("epoch", _LAST_AT - func.min(Trace.started_at)) * 1000).label("duration_ms"),
)
_PAGE_COLUMNS = (
    Trace.session_id,
    func.count(distinct(Trace.external_user_id)).label("user_count"),
    # The newest turn, as the turns endpoint orders them.
    func.array_agg(
        aggregate_order_by(Trace.trace_id, Trace.started_at.desc(), Trace.trace_id.desc()),
        type_=ARRAY(Text),
    )[1].label("last_trace_id"),
)

TOOL_CALLS = text(
    """
    SELECT trace_id, count(*) AS tool_calls
    FROM spans
    WHERE project_id = :project_id AND trace_id = ANY(:trace_ids) AND kind = 'tool'
    GROUP BY trace_id
    """
).bindparams(bindparam("trace_ids", type_=ARRAY(String)))


def _in_window(
    project_id: uuid.UUID, start: datetime, end: datetime, environment: str | None
) -> list[ColumnElement[bool]]:
    conditions = [
        Trace.project_id == project_id,
        Trace.session_id.is_not(None),
        Trace.started_at >= start,
        Trace.started_at < end,
    ]
    if environment is not None:
        # Before the grouping: every sum covers this environment's traces only.
        conditions.append(Trace.environment == environment)
    return conditions


def _in(column: ColumnElement[Any] | Any, order: SortOrder) -> Any:
    return column.asc() if order == "asc" else column.desc()


def _after(sort: SessionSort, order: SortOrder, cursor: str) -> ColumnElement[bool]:
    """The sessions after the cursor's in the sort's order."""
    after = operator.gt if order == "asc" else operator.lt
    if sort == "last":
        last_at, session_id = decode_cursor(cursor)
        beyond: ColumnElement[bool] = after(
            tuple_(_LAST_AT, Trace.session_id), tuple_(last_at, session_id)
        )
        return beyond
    bound, session_id = decode_value_cursor(cursor)
    value = _VALUES[sort]
    later_in_tie = after(Trace.session_id, session_id)
    if bound is None:
        return and_(value.is_(None), later_in_tie)
    return or_(after(value, bound), and_(value == bound, later_in_tie), value.is_(None))


async def read_sessions(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: tuple[datetime, datetime],
    *,
    environment: str | None,
    q: str | None,
    sort: SessionSort,
    order: SortOrder,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of sessions (`SessionListItemOut` fields but the preview) and the cursor of the
    next (None on the last page). `q` keeps the sessions whose id starts with it (ignoring case,
    `%_\\` literal) or with a trace of that user id."""
    where = _in_window(project_id, *window, environment)
    query = select(*_COLUMNS).where(*where).group_by(Trace.session_id).limit(limit + 1)
    if sort == "last":
        query = query.order_by(_in(_LAST_AT, order), _in(Trace.session_id, order))
    else:
        value = _VALUES[sort]
        query = query.add_columns(value.label("sort_value"))
        query = query.order_by(_in(value, order).nulls_last(), _in(Trace.session_id, order))
    if q is not None:
        query = query.having(
            or_(
                Trace.session_id.ilike(f"{escape_like(q)}%", escape="\\"),
                func.bool_or(Trace.external_user_id == q),
            )
        )
    if cursor:
        query = query.having(_after(sort, order, cursor))
    rows = (await db.execute(query)).all()
    page = rows[:limit]
    ids = [row.session_id for row in page]
    details = await _page_details(db, where, ids)
    # A session whose traces were deleted between the two statements (retention) has no details.
    missing = {"user_count": 0, "last_trace_id": None}
    items = [{**row._mapping, **details.get(row.session_id, missing)} for row in page]
    return items, _next_cursor(page[-1], sort) if len(rows) > limit else None


async def _page_details(
    db: AsyncSession, where: list[ColumnElement[bool]], ids: list[str]
) -> dict[str, Any]:
    """`_PAGE_COLUMNS` of the page's sessions, over the same traces, by session id."""
    if not ids:
        return {}
    in_page = Trace.session_id == func.any(bindparam("ids", ids, ARRAY(Text)))
    query = select(*_PAGE_COLUMNS).where(*where, in_page).group_by(Trace.session_id)
    return {row.session_id: row._mapping for row in await db.execute(query)}


def _next_cursor(row: Row[Any], sort: SessionSort) -> str:
    if sort == "last":
        return encode_cursor(row.last_at, row.session_id)
    return encode_value_cursor(row.sort_value, row.session_id)


async def read_cost_p99(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: tuple[datetime, datetime],
    environment: str | None,
) -> Decimal | None:
    """The 99th percentile (`percentile_cont`) of the window's session costs, unknown costs left
    out; None with fewer than `P99_MIN_SESSIONS` sessions that have one. No search: the bar is
    the window's."""
    costs = (
        select(func.sum(Trace.cost_usd).label("cost"))
        .where(*_in_window(project_id, *window, environment))
        .group_by(Trace.session_id)
        .subquery()
    )
    # A session sums many traces, so it can pass the per-trace column's $1M bound; 8 decimals
    # still round off percentile_cont's double precision.
    p99 = cast(func.percentile_cont(0.99).within_group(costs.c.cost), Numeric(20, 8))
    value: Decimal | None = await db.scalar(
        select(case((func.count(costs.c.cost) >= P99_MIN_SESSIONS, p99)))
    )
    return value


async def tool_calls(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> dict[str, int]:
    """Tool spans per trace, by trace id (absent: none). One query; none for no ids."""
    if not trace_ids:
        return {}
    rows = await db.execute(TOOL_CALLS, {"project_id": project_id, "trace_ids": list(trace_ids)})
    return {trace_id: count for trace_id, count in rows}
