"""Sessions: the traces that share a `session_id`, and each session's turns.

All queries run in a transaction bound to the project (row-level security)
and also filter on `project_id` explicitly, which keeps index usage obvious.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.params import NO_NUL, Environment
from app.api.schemas import Page, SpanOut
from app.api.schemas.traces import SessionListItemOut, SessionPage, SessionTurnOut
from app.api.trace_filters import OptionalText
from app.api.v1.traces import NO_DETAILS, ProjectReader, trace_summary
from app.api.window import Window
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.wire import unescape_path_name
from app.db.models import Trace
from app.explorer import session_queries, trace_sort
from app.explorer.session_queries import SessionSort
from app.explorer.trace_previews import trace_previews
from app.explorer.trace_queries import summary_details, turn_spans
from app.explorer.trace_sort import SortOrder
from app.scores.queries import latest_scores_by_name

router = APIRouter(prefix="/projects/{project_id}", tags=["traces"])


@router.get("/sessions", response_model=SessionPage)
async def list_sessions(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: OptionalText = None,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=256,
            pattern=NO_NUL,
            description="Sessions whose id starts with it (ignoring case) or with a trace of "
            "exactly this user id.",
        ),
    ] = None,
    sort: SessionSort = "last",
    order: SortOrder = "desc",
    preview: Annotated[
        bool, Query(description="Fill `last_input_preview` (null otherwise).")
    ] = False,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> SessionPage:
    project = access.require_project()
    bounds = (window.start, window.end)
    rows, next_cursor = await session_queries.read_sessions(
        db,
        project.id,
        bounds,
        environment=environment,
        q=q,
        sort=sort,
        order=order,
        limit=limit,
        cursor=cursor,
    )
    last_ids = [row["last_trace_id"] for row in rows if row["last_trace_id"] is not None]
    previews = await trace_previews(db, project.id, last_ids) if preview else {}
    items = [
        SessionListItemOut.model_validate(
            {**row, "last_input_preview": previews.get(row["last_trace_id"], (None, None))[0]}
        )
        for row in rows
    ]
    return SessionPage(
        items=items,
        next_cursor=next_cursor,
        # The first page only: "Load more" would repeat the same full grouping for the same figure.
        cost_p99_usd=None
        if cursor
        else await session_queries.read_cost_p99(db, project.id, bounds, environment),
    )


# `:path` lets an id that contains a slash through; `.`, `..` and ids starting with `~` arrive
# `~`-escaped (`unescape_path_name`), as on the users routes. 256 is `TraceIn.session_id`'s limit.
@router.get("/sessions/{session_id:path}/turns", response_model=Page[SessionTurnOut])
async def list_session_turns(
    project_id: uuid.UUID,
    session_id: Annotated[str, Path(min_length=1, max_length=257, pattern=NO_NUL)],
    access: ProjectReader,
    db: DbSession,
    environment: Environment = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[SessionTurnOut]:
    """The session's traces as `GET /traces?session_id=` lists them (newest first, same cursor),
    each with the few spans its conversation turn is read from. No time window: the session's
    traces as long as they are retained."""
    project = access.require_project()
    session_id = unescape_path_name(session_id)
    page_query = select(Trace).where(Trace.project_id == project.id, Trace.session_id == session_id)
    if environment is not None:
        page_query = page_query.where(Trace.environment == environment)
    page_query = trace_sort.apply(page_query, sort="started_at", order="desc", cursor=cursor).limit(
        limit + 1
    )
    rows = (await db.scalars(page_query)).all()
    page = rows[:limit]
    next_cursor = trace_sort.next_cursor(page[-1], "started_at") if len(rows) > limit else None
    trace_ids = [trace.trace_id for trace in page]
    details = await summary_details(db, project.id, trace_ids)
    scores = await latest_scores_by_name(db, project.id, trace_ids)
    spans = await turn_spans(db, project.id, trace_ids)
    tools = await session_queries.tool_calls(db, project.id, trace_ids)
    return Page[SessionTurnOut](
        items=[
            SessionTurnOut(
                trace=trace_summary(
                    trace,
                    *details.get(trace.trace_id, NO_DETAILS),
                    scores.get(trace.trace_id, []),
                ),
                spans=[
                    SpanOut.model_validate(span, from_attributes=True).model_copy(
                        update={"payload_omitted": omitted}
                    )
                    for span, omitted in spans.get(trace.trace_id, [])
                ],
                tool_calls=tools.get(trace.trace_id, 0),
            )
            for trace in page
        ],
        next_cursor=next_cursor,
    )
