"""Two traces compared step by step (`GET /traces/compare`).

A span's key is its name and occurrence: the first span of a name in start order is occurrence
1, the next one 2. Spans of the two traces pair when their keys match, which is what a re-run of
the same code reproduces; span ids and timings never match across runs. Parallel calls of one
name that start at the same instant are ordered by span id, which differs between runs, so they
may pair crosswise. Inputs and outputs are compared by an md5 of their JSONB text, computed in
the database, so payloads never leave it (JSONB text is canonical: equal JSON gives equal text).
"""

import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Span

# The most spans of one trace that are compared (the export batch size). The query reads no
# payload, so this bounds the response, not the database work per span.
MAX_COMPARE_SPANS = 2000

StepKey = tuple[str, int]
# One span as `compare_spans` reads it.
CompareSpan = Row[*tuple[Any, ...]]


async def compare_spans(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str
) -> tuple[Sequence[CompareSpan], bool]:
    """The trace's first `MAX_COMPARE_SPANS` spans in start order, and whether more were cut."""
    rows = (
        await db.execute(
            select(
                Span.span_id,
                Span.name,
                Span.kind,
                Span.status,
                Span.model,
                Span.duration_ms,
                Span.input_tokens,
                Span.output_tokens,
                Span.cost_usd,
                Span.truncated,
                func.md5(cast(Span.input, Text)).label("input_hash"),
                func.md5(cast(Span.output, Text)).label("output_hash"),
            )
            .where(Span.project_id == project_id, Span.trace_id == trace_id)
            .order_by(Span.started_at, Span.span_id)
            .limit(MAX_COMPARE_SPANS + 1)
        )
    ).all()
    return rows[:MAX_COMPARE_SPANS], len(rows) > MAX_COMPARE_SPANS


def _keyed(spans: Sequence[CompareSpan]) -> list[tuple[StepKey, CompareSpan]]:
    seen: Counter[str] = Counter()
    keyed = []
    for span in spans:
        seen[span.name] += 1
        keyed.append(((span.name, seen[span.name]), span))
    return keyed


def align(
    a: Sequence[CompareSpan], b: Sequence[CompareSpan]
) -> list[tuple[StepKey, CompareSpan | None, CompareSpan | None]]:
    """Pair the spans of two traces by key, in trace A's order.

    A step only in B goes right after the last step before it (in B's order) that A also has,
    or first when there is none.
    """
    a_keyed, b_keyed = _keyed(a), _keyed(b)
    a_spans, b_spans = dict(a_keyed), dict(b_keyed)
    a_index = {key: index for index, (key, _) in enumerate(a_keyed)}
    position: dict[StepKey, tuple[int, int, int]] = {key: (i, 0, 0) for key, i in a_index.items()}
    anchor = -1
    for index, (key, _) in enumerate(b_keyed):
        if key in a_index:
            anchor = a_index[key]
        else:
            position[key] = (anchor, 1, index)
    ordered = sorted(position, key=position.__getitem__)
    return [(key, a_spans.get(key), b_spans.get(key)) for key in ordered]


def payload_changed(a: CompareSpan | None, b: CompareSpan | None, hash_name: str) -> bool | None:
    """Whether the input (`input_hash`) or output (`output_hash`) differs; None when unknowable.

    Unknown: a side is missing or did not capture the payload, or the payloads match but a span
    was cut at ingest (`truncated` covers input and output), so the part after the cut is unknown.
    """
    if a is None or b is None:
        return None
    before, after = getattr(a, hash_name), getattr(b, hash_name)
    if before is None or after is None:
        return None
    if before != after:
        return True
    return None if a.truncated or b.truncated else False
