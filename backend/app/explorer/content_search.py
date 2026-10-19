"""Free-text search of the trace list and exports: trace name, trace id and span content.

`q` matches a trace when its name contains `q` (ignoring case), when `q` is its id, or when one
of its spans in the window carries `q` in a preview of its input or output. The preview is the
last 2,000 characters of the input and the first 2,000 of the output as JSON text, indexed with
`pg_trgm` by migration 1006 (which also explains why the search functions are LEAKPROOF).
"""

import uuid
from datetime import datetime

from sqlalchemy import Text, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Span, Trace
from app.explorer.trace_queries import escape_like

# The function `spans_content_trgm_idx` indexes (migration 1006), called on the same columns so
# the planner matches the index.
SEARCH_TEXT = func.spanlight_search_text(Span.input, Span.output, type_=Text)
# A trigram index needs three characters to narrow anything; shorter text searches names only.
MIN_CONTENT_QUERY = 3


def text_search(
    q: str, project_id: uuid.UUID, start: datetime, end: datetime
) -> ColumnElement[bool]:
    """The traces (started in `[start, end)`) whose name, id or span content matches `q`."""
    pattern = f"%{escape_like(q)}%"
    matches = [Trace.name.ilike(pattern, escape="\\"), Trace.trace_id == q.lower()]
    if len(q) >= MIN_CONTENT_QUERY:
        # A trace's spans start at or after the trace; a span that starts after `end` is not
        # searched, which keeps the scan inside the window.
        matching_spans = select(Span.trace_id).where(
            Span.project_id == project_id,
            Span.started_at >= start,
            Span.started_at < end,
            SEARCH_TEXT.ilike(pattern, escape="\\"),
        )
        matches.append(Trace.trace_id.in_(matching_spans))
    return or_(*matches)
