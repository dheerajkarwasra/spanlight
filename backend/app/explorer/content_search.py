"""Free-text search of the trace list and exports: trace name, trace id and span content.

`q` matches a trace when its name contains `q` (ignoring case), when `q` is its id, or when one
of its spans in the window carries `q` in a preview of its input or output. The preview is the
last 2,000 characters of the input and the first 2,000 of the output as JSON text, indexed with
`pg_trgm` by migration 1007 (which also explains why the search functions are LEAKPROOF).

Row-level security lets Postgres use that index only when both search functions are marked
LEAKPROOF, which needs a superuser at migration time. Without the marks every search would scan
the spans of the whole window, so content is then searched in the window's last hour only.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Text, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ProblemError
from app.db.models import Span, Trace
from app.explorer.trace_queries import escape_like

# The function `spans_content_trgm_idx` indexes (migration 1007), called on the same columns so
# the planner matches the index.
SEARCH_TEXT = func.spanlight_search_text(Span.input, Span.output, type_=Text)
# A trigram index needs three characters to narrow anything; shorter text searches names only.
MIN_CONTENT_QUERY = 3
# Whether the planner may use the index under row-level security: both search functions are
# marked LEAKPROOF. Read from the catalog for every search, so a restored database that lost the
# mark (`spanlight ensure-app-role` sets it again) is never taken for an indexed one.
INDEX_USABLE = text(
    "SELECT coalesce(bool_and(proleakproof), false) FROM pg_catalog.pg_proc WHERE oid IN ("
    "'pg_catalog.texticlike(text, text)'::regprocedure, "
    "to_regprocedure('spanlight_search_text(jsonb, jsonb)'))"
)
# A trace-list search that runs longer is stopped and answered `422 SEARCH_TOO_BROAD`, inside the
# API's statement timeout, so a broad search gets a clear answer instead of a 503 to retry.
SEARCH_TIMEOUT_MS = 5000
# How far back content is searched when the index cannot be used.
UNINDEXED_SPAN = timedelta(hours=1)


async def content_start(db: AsyncSession, start: datetime, end: datetime) -> datetime:
    """Where content search starts for the window `[start, end)`.

    `start`, or the window's last hour when the index cannot be used. A plain timestamp, not a
    condition in the query, so the planner bounds the span scan with the time index.
    """
    usable = await db.scalar(INDEX_USABLE)
    return start if usable else max(start, end - UNINDEXED_SPAN)


def text_search(
    q: str, project_id: uuid.UUID, content_from: datetime, end: datetime
) -> ColumnElement[bool]:
    """The traces whose name, id or content of a span started in `[content_from, end)` match `q`.

    The caller limits the traces to its window; `content_from` comes from `content_start`.
    """
    # Always ends with `%`, never with an escape character: the LEAKPROOF mark on `texticlike`
    # (migration 1007) relies on the app never sending a pattern that can fail on row data.
    pattern = f"%{escape_like(q)}%"
    matches = [Trace.name.ilike(pattern, escape="\\"), Trace.trace_id == q.lower()]
    if len(q) >= MIN_CONTENT_QUERY:
        # A trace's spans start at or after the trace; a span that starts after `end` is not
        # searched, which keeps the scan inside the window.
        matching_spans = select(Span.trace_id).where(
            Span.project_id == project_id,
            Span.started_at >= content_from,
            Span.started_at < end,
            SEARCH_TEXT.ilike(pattern, escape="\\"),
        )
        matches.append(Trace.trace_id.in_(matching_spans))
    return or_(*matches)


def search_too_broad() -> ProblemError:
    return ProblemError(
        422,
        "SEARCH_TOO_BROAD",
        "This search matches too much to finish. Narrow the time range or search for more "
        "specific text.",
    )
