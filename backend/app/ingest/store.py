"""The ingestion writes: upsert traces and spans on their natural keys, then recompute rollups.

Idempotent: replaying a batch leaves the same rows, since every trace total is recomputed from
the stored spans. Rows are written in sorted key order, so batches touching several traces
cannot deadlock each other. The caller (`app.ingest.pipeline.ingest_spans`) has bound the
project and share-locked its row, and commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Score, Span, Trace
from app.ingest.normalize import NormalizedSpan


@dataclass
class _TraceRow:
    trace_id: str
    started_at: datetime
    ended_at: datetime
    name: str | None = None
    environment: str | None = None
    release: str | None = None
    external_user_id: str | None = None
    session_id: str | None = None
    tags: set[str] = field(default_factory=set)

    def merge(self, span: NormalizedSpan) -> None:
        """Trace-level fields: within a batch, the last non-null value wins."""
        self.started_at = min(self.started_at, span.started_at)
        self.ended_at = max(self.ended_at, span.ended_at)
        fields = span.trace
        self.name = fields.name or self.name
        self.environment = fields.environment or self.environment
        self.release = fields.release or self.release
        self.external_user_id = fields.external_user_id or self.external_user_id
        self.session_id = fields.session_id or self.session_id
        self.tags.update(fields.tags)


def _merge_traces(spans: Sequence[NormalizedSpan]) -> list[_TraceRow]:
    rows: dict[str, _TraceRow] = {}
    for span in spans:
        row = rows.get(span.trace_id)
        if row is None:
            row = _TraceRow(span.trace_id, span.started_at, span.ended_at)
            rows[span.trace_id] = row
        row.merge(span)
    return [rows[trace_id] for trace_id in sorted(rows)]


async def write_spans(
    session: AsyncSession,
    project_id: uuid.UUID,
    spans: Sequence[NormalizedSpan],
    source_key_id: uuid.UUID | None,
) -> None:
    """Upsert the traces, then the spans, then recompute the traces' totals from stored spans.

    `source_key_id` is the gateway key the spans came through; None for native and OTLP spans.
    """
    traces = _merge_traces(spans)
    await _upsert_traces(session, project_id, traces)
    await _upsert_spans(session, project_id, spans, source_key_id)
    await _recompute_rollups(session, project_id, [trace.trace_id for trace in traces])


async def _upsert_traces(
    session: AsyncSession, project_id: uuid.UUID, traces: Sequence[_TraceRow]
) -> None:
    statement = insert(Trace).values(
        [
            {
                "project_id": project_id,
                "trace_id": trace.trace_id,
                "name": trace.name,
                "environment": trace.environment,
                "release": trace.release,
                "external_user_id": trace.external_user_id,
                "session_id": trace.session_id,
                "tags": sorted(trace.tags),
                "started_at": trace.started_at,
                "ended_at": trace.ended_at,
                # A score may have arrived before its trace. On conflict the flag is OR-ed in,
                # so a later batch also heals a flag a racing score write missed.
                "has_scores": exists().where(
                    Score.project_id == project_id, Score.trace_id == trace.trace_id
                ),
            }
            for trace in traces
        ]
    )
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[Trace.project_id, Trace.trace_id],
        set_={
            "name": func.coalesce(excluded.name, Trace.name),
            "environment": func.coalesce(excluded.environment, Trace.environment),
            "release": func.coalesce(excluded.release, Trace.release),
            "external_user_id": func.coalesce(excluded.external_user_id, Trace.external_user_id),
            "session_id": func.coalesce(excluded.session_id, Trace.session_id),
            "tags": text(
                "ARRAY(SELECT DISTINCT tag FROM unnest(traces.tags || excluded.tags) AS tag "
                "ORDER BY tag)"
            ),
            "started_at": func.least(Trace.started_at, excluded.started_at),
            "ended_at": func.greatest(Trace.ended_at, excluded.ended_at),
            "has_scores": Trace.has_scores | excluded.has_scores,
        },
    )
    await session.execute(statement)


_SPAN_COLUMNS = (
    "parent_span_id",
    "kind",
    "name",
    "status",
    "status_message",
    "error_class",
    "started_at",
    "ended_at",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cost_usd",
    "pricing_version",
    "time_to_first_token_ms",
    "input",
    "output",
    "attributes",
    "truncated",
    "request_hash",
    "finish_reason",
    "prompt_version_id",
)


async def _upsert_spans(
    session: AsyncSession,
    project_id: uuid.UUID,
    spans: Sequence[NormalizedSpan],
    source_key_id: uuid.UUID | None,
) -> None:
    ordered = sorted(spans, key=lambda span: (span.trace_id, span.span_id))
    statement = insert(Span).values(
        [
            {
                "project_id": project_id,
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "source_key_id": source_key_id,
                **{column: getattr(span, column) for column in _SPAN_COLUMNS},
            }
            for span in ordered
        ]
    )
    update_columns: dict[str, Any] = {
        column: statement.excluded[column] for column in _SPAN_COLUMNS
    }
    update_columns["source_key_id"] = statement.excluded.source_key_id
    update_columns["ingested_at"] = func.now()
    # A span the gateway recorded (its `source_key_id` is set) is the gateway's: native and OTLP
    # ingestion never overwrite it, so a replayed SDK span with the same id cannot rewrite the
    # cost or tokens a gateway key is accountable for. The span is counted as accepted and left
    # as it is, so a replay is still idempotent.
    not_gateway_owned = Span.source_key_id.is_(None) if source_key_id is None else None
    statement = statement.on_conflict_do_update(
        index_elements=[Span.project_id, Span.trace_id, Span.span_id],
        set_=update_columns,
        where=not_gateway_owned,
    )
    await session.execute(statement)


_RECOMPUTE_ROLLUPS = text(
    """
    UPDATE traces AS t
    SET span_count    = s.span_count,
        error_count   = s.error_count,
        input_tokens  = s.input_tokens,
        output_tokens = s.output_tokens,
        cost_usd      = s.cost_usd,
        has_unpriced  = s.has_unpriced,
        started_at    = s.started_at,
        ended_at      = s.ended_at
    FROM (
        SELECT trace_id,
               count(*)                                   AS span_count,
               count(*) FILTER (WHERE status = 'error')   AS error_count,
               coalesce(sum(input_tokens), 0)             AS input_tokens,
               coalesce(sum(output_tokens), 0)            AS output_tokens,
               sum(cost_usd)                              AS cost_usd,
               coalesce(
                   bool_or((kind = 'llm' OR model IS NOT NULL) AND cost_usd IS NULL),
                   false
               )                                          AS has_unpriced,
               min(started_at)                            AS started_at,
               max(ended_at)                              AS ended_at
        FROM spans
        WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
        GROUP BY trace_id
    ) AS s
    WHERE t.project_id = :project_id AND t.trace_id = s.trace_id
    """
)


async def _recompute_rollups(
    session: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> None:
    await session.execute(
        _RECOMPUTE_ROLLUPS, {"project_id": project_id, "trace_ids": list(trace_ids)}
    )
