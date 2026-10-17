"""Seeding for the readers that must leave usage rollups out of their sums (spec §5.4).

`ingest_rollup_pair` stores one trace `parent(llm, usage, priced, failed) -> child(llm, usage,
priced)` through the real pipeline, so the parent is flagged a usage rollup by the ingest
recompute. A reader's token and cost sums must then equal the child's figures alone, while its
span, call and error counts still see both spans.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select

from app.db.models import Span
from app.db.rls import bypass_rls
from tests.helpers import Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory


@dataclass(frozen=True, slots=True)
class RollupPair:
    workspace: Workspace
    project_id: uuid.UUID
    trace_id: str
    parent_span_id: str
    child_span_id: str


@dataclass(frozen=True, slots=True)
class ChildFigures:
    """The stored tokens and cost of the child: what every sum must come to."""

    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


async def ingest_rollup_pair(
    browser_factory: Any, client: httpx.AsyncClient, start: datetime, **overrides: Any
) -> RollupPair:
    """Store the pair in a fresh workspace; `overrides` go on both span bodies."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    trace_id = new_trace_id()
    parent = span(trace_id=trace_id, start=start, status="error", duration_ms=2000, **overrides)
    child = span(
        trace_id=trace_id,
        start=start,
        parent_span_id=parent["span_id"],
        usage={"input_tokens": 300, "output_tokens": 70, "cached_tokens": 0},
        **overrides,
    )
    response = await client.post(
        "/v1/traces", json={"spans": [parent, child]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text
    return RollupPair(
        workspace, uuid.UUID(workspace.project_id), trace_id, parent["span_id"], child["span_id"]
    )


async def child_figures(session_factory: SessionFactory, pair: RollupPair) -> ChildFigures:
    """The child's stored figures, after checking the parent is the flagged rollup."""
    async with session_factory() as db:
        await bypass_rls(db)
        rows = (
            await db.execute(
                select(
                    Span.span_id, Span.usage_rollup, Span.input_tokens, Span.output_tokens,
                    Span.cost_usd,
                ).where(Span.trace_id == pair.trace_id)
            )
        ).all()  # fmt: skip
    by_id = {row.span_id: row for row in rows}
    assert by_id[pair.parent_span_id].usage_rollup is True
    child = by_id[pair.child_span_id]
    assert child.usage_rollup is False and child.cost_usd is not None
    return ChildFigures(child.input_tokens, child.output_tokens, child.cost_usd)
