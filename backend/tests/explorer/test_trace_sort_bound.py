"""A `started_at` cursor gives the index a range bound, so a deep page does not re-read page one."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.pagination import encode_cursor
from app.db.models import Trace
from app.explorer.trace_sort import SortOrder, apply


@pytest.mark.parametrize(("order", "bound"), [("desc", "<="), ("asc", ">=")])
def test_started_at_cursor_bounds_the_index_scan(order: SortOrder, bound: str) -> None:
    """Postgres cannot turn `a < x OR (a = x AND b < y)` into an index bound on its own."""
    cursor = encode_cursor(datetime(2026, 11, 1, tzinfo=UTC), "ab" * 16)
    query = apply(select(Trace.trace_id), sort="started_at", order=order, cursor=cursor)
    sql = str(query.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert f"traces.started_at {bound} " in sql
