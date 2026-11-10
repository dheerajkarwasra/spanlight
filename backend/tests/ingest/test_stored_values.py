"""What ingestion stores for values that are easy to lose between Python and Postgres.

The rows travel as one JSON parameter (`app.ingest.store._rows`); these are the values a JSON
round trip could change: SQL NULL against a JSON null, payloads that are not objects, text that
needs escaping in JSON or in an array, exact decimals, and timestamps with an offset.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.rls import bind_project
from app.ingest.pipeline import IngestTarget, ingest_spans
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace, new_trace_id, span

TAGS = ['say "hi"', "a,b", "back\\slash", "NULL", "naïve ☃", "{curly}", "new\nline"]
PAYLOADS: list[Any] = [
    None,
    "null",
    "plain text",
    0,
    False,
    [],
    [1, "two", None],
    {"k": None, "quote": 'a "b" \\ c', "n": 12345678901234567890, "f": 0.1, "u": "☃ \u2028"},
]
START = datetime.now(UTC).replace(microsecond=123456) - timedelta(minutes=10)


async def test_values_are_stored_as_sent(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("stored-values@example.com"))
    project = uuid.UUID(workspace.project_id)
    target = IngestTarget(project, True, uuid.UUID(workspace.org_id))
    trace_id = new_trace_id()
    offset = START.astimezone().isoformat()  # the machine's offset, not UTC
    spans = [
        span(
            trace_id=trace_id,
            span_id=f"{index + 1:016x}",
            start=START,
            input=payload,
            output=payload,
            attributes={"note": 'q"uote', "nested": {"a": [1, None]}},
            time_to_first_token_ms=0.1 + index,
            usage={"input_tokens": 1_000_003, "output_tokens": 7, "cached_tokens": 0},
            trace={"name": "it's", "tags": TAGS, "metadata": {"k": 'v "1"', "tier": "gold"}},
        )
        for index, payload in enumerate(PAYLOADS)
    ]
    spans[0]["start_time"] = offset
    async with session_factory() as db:
        outcome = await ingest_spans(db, target, spans)
        await db.commit()
    assert outcome.accepted == len(PAYLOADS) and not outcome.rejected

    async with session_factory() as db:
        await bind_project(db, project)
        stored = (
            await db.execute(
                text(
                    "SELECT input, output, input IS NULL AS no_input, attributes, started_at, "
                    "cost_usd, time_to_first_token_ms, input_tokens "
                    "FROM spans WHERE trace_id = :trace_id ORDER BY span_id"
                ),
                {"trace_id": trace_id},
            )
        ).all()
        trace = (
            await db.execute(
                text("SELECT name, tags, metadata, started_at FROM traces WHERE trace_id = :id"),
                {"id": trace_id},
            )
        ).one()

    assert [row.input for row in stored] == PAYLOADS
    assert [row.output for row in stored] == PAYLOADS
    # A missing payload is SQL NULL; the text "null" is a JSON string.
    assert [row.no_input for row in stored] == [True, *[False] * (len(PAYLOADS) - 1)]
    assert {row.started_at for row in stored} == {START}
    assert all(row.attributes == {"note": 'q"uote', "nested": {"a": [1, None]}} for row in stored)
    assert [row.time_to_first_token_ms for row in stored] == [
        0.1 + index for index in range(len(PAYLOADS))
    ]
    assert {row.input_tokens for row in stored} == {1_000_003}
    # gpt-4o-mini: 1,000,003 input and 7 output tokens, to the eighth decimal.
    assert {row.cost_usd for row in stored} == {Decimal("0.15000465")}
    assert trace.name == "it's"
    assert trace.tags == sorted(TAGS)
    assert trace.metadata == {"k": 'v "1"', "tier": "gold"}
    assert trace.started_at == START
