"""Input and output previews for a page of the trace list (`GET /traces?preview=true`).

Per field the root span's own payload (no parent, or a parent not stored, as `TURN_SPAN_PICKS`
finds it), else the first llm span's input and the last llm span's output. Only the start of
each payload's JSON text leaves the database: `left(…::text, 4000)` still reads the whole JSONB,
but the response and the Python side stay small. The cut is taken outside the picks, so only the
chosen payload is serialized (inside, the planner computed it for every candidate before the
top-1 sort), and the llm picks run only when the root has no payload on that side (a one-time
filter on the root's column). For a chat input (`messages`, or a list of messages) only the last
user message is serialized and cut, so a long system prompt or tool list does not fill the 4000
characters and every agent row look alike; without a user message the whole input is.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import ARRAY, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.explorer.preview_text import PREVIEW_SOURCE_CHARS, preview_text

TRACE_PREVIEWS = (
    text(
        f"""
        SELECT p.trace_id,
               left(coalesce(
                   jsonb_path_query_array(p.input, '$.messages[*] ? (@.role == "user")') -> -1,
                   jsonb_path_query_array(p.input, '$[*] ? (@.role == "user")') -> -1,
                   p.input
               )::text, {PREVIEW_SOURCE_CHARS}) AS input,
               left(p.output::text, {PREVIEW_SOURCE_CHARS}) AS output
        FROM (SELECT t.trace_id,
                     coalesce(r.input, first_llm.input) AS input,
                     coalesce(r.output, last_llm.output) AS output
        FROM unnest(:trace_ids) AS t(trace_id)
        LEFT JOIN LATERAL (
            SELECT s.input, s.output
            FROM spans AS s
            WHERE s.project_id = :project_id AND s.trace_id = t.trace_id
              AND (s.parent_span_id IS NULL OR NOT EXISTS (
                  SELECT 1 FROM spans AS parent
                   WHERE parent.project_id = s.project_id AND parent.trace_id = s.trace_id
                     AND parent.span_id = s.parent_span_id))
            ORDER BY s.started_at, s.span_id LIMIT 1
        ) AS r ON true
        LEFT JOIN LATERAL (
            SELECT s.input
            FROM spans AS s
            WHERE r.input IS NULL
              AND s.project_id = :project_id AND s.trace_id = t.trace_id
              AND s.kind = 'llm' AND s.input IS NOT NULL
            ORDER BY s.started_at, s.span_id LIMIT 1
        ) AS first_llm ON true
        LEFT JOIN LATERAL (
            SELECT s.output
            FROM spans AS s
            WHERE r.output IS NULL
              AND s.project_id = :project_id AND s.trace_id = t.trace_id
              AND s.kind = 'llm' AND s.output IS NOT NULL
            ORDER BY s.started_at DESC, s.span_id DESC LIMIT 1
        ) AS last_llm ON true
        ) AS p
        """
    )
    .bindparams(bindparam("trace_ids", type_=ARRAY(String)))
    .columns(trace_id=String, input=String, output=String)
)


async def trace_previews(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> dict[str, tuple[str | None, str | None]]:
    """(input preview, output preview) by trace id. One query; none for no ids."""
    if not trace_ids:
        return {}
    rows = await db.execute(
        TRACE_PREVIEWS, {"trace_ids": list(trace_ids), "project_id": project_id}
    )
    return {
        trace_id: (preview_text(raw_input, role="input"), preview_text(raw_output, role="output"))
        for trace_id, raw_input, raw_output in rows
    }
