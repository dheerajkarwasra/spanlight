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

from sqlalchemy import Table, exists, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert

from app.db.models import Score, Span, Trace
from app.ingest.metadata import merge_metadata
from app.ingest.normalize import NormalizedSpan
from app.ingest.usage_rollup import recompute_usage_rollups


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
    metadata: dict[str, str] = field(default_factory=dict)

    def merge(self, span: NormalizedSpan) -> None:
        """Trace-level fields: within a batch, the last non-null value wins.

        Metadata merges per key by the same rule as the upsert (`merge_metadata`, spec §4.4).
        """
        self.started_at = min(self.started_at, span.started_at)
        self.ended_at = max(self.ended_at, span.ended_at)
        fields = span.trace
        self.name = fields.name or self.name
        self.environment = fields.environment or self.environment
        self.release = fields.release or self.release
        self.external_user_id = fields.external_user_id or self.external_user_id
        self.session_id = fields.session_id or self.session_id
        self.tags.update(fields.tags)
        self.metadata = merge_metadata(self.metadata, fields.metadata)


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
                "metadata": trace.metadata,
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
            # Keyed by the column name: the ORM attribute is `metadata_` (`Base.metadata` is
            # reserved), but the insert's column key is `metadata`.
            "metadata": text("merge_trace_metadata(traces.metadata, excluded.metadata)"),
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
    "provider_error_type",
    "provider_error_code",
    "rate_limit_kind",
    "started_at",
    "ended_at",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "service_tier",
    "web_search_requests",
    "cost_usd",
    "fee_usd",
    "pricing_version",
    "time_to_first_token_ms",
    "input",
    "output",
    "attributes",
    "truncated",
    "payload_bytes",
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
    rows = [
        {
            "project_id": project_id,
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "source_key_id": source_key_id,
            **{column: getattr(span, column) for column in _SPAN_COLUMNS},
        }
        for span in ordered
    ]
    statement = _SPAN_UPSERT_GUARDED if source_key_id is None else _SPAN_UPSERT
    await session.execute(statement, rows)


def _span_upsert(*, guarded: bool) -> ReturningInsert[tuple[str]]:
    """The span upsert, compiled once and given the rows as parameters.

    `RETURNING` makes SQLAlchemy render the rows into one multi-row INSERT per 1,000 rows
    ("insertmanyvalues"), in the given order. Inlining the rows with `.values([...])` instead
    compiled every column of every row again for each batch, the largest CPU cost of ingestion.
    """
    spans: Table = Span.__table__  # type: ignore[assignment]  # mapped classes type it loosely
    statement = insert(spans)
    update_columns: dict[str, Any] = {
        column: statement.excluded[column] for column in _SPAN_COLUMNS
    }
    update_columns["source_key_id"] = statement.excluded.source_key_id
    update_columns["ingested_at"] = func.now()
    # A span the gateway recorded (its `source_key_id` is set) is the gateway's: native and OTLP
    # ingestion never overwrite it, so a replayed SDK span with the same id cannot rewrite the
    # cost or tokens a gateway key is accountable for. The span is counted as accepted and left
    # as it is, so a replay is still idempotent.
    return statement.on_conflict_do_update(
        index_elements=[spans.c.project_id, spans.c.trace_id, spans.c.span_id],
        set_=update_columns,
        where=spans.c.source_key_id.is_(None) if guarded else None,
    ).returning(spans.c.span_id)


_SPAN_UPSERT_GUARDED = _span_upsert(guarded=True)  # native and OTLP spans
_SPAN_UPSERT = _span_upsert(guarded=False)  # the gateway's own spans


# Retry calls and retry cost: among a trace's llm spans with a request hash, cache
# hits and fault-injected spans left out, every span of a hash but the last by
# `(started_at, span_id)` is superseded. `retry_cost_usd` is unknown (NULL) when any superseded
# span is unpriced, and, like `cost_usd`, when the sum is too large for numeric(14, 8). A trace
# with no superseded span has a known zero of both. The project id is hidden from the planner as
# `(SELECT CAST(:project_id AS uuid))`, for the reason in `app.ingest.usage_rollup`.
_RECOMPUTE_ROLLUPS = text(
    """
    WITH ranked AS (
        SELECT trace_id,
               cost_usd,
               row_number() OVER (
                   PARTITION BY trace_id, request_hash ORDER BY started_at DESC, span_id DESC
               ) AS position
        FROM spans
        WHERE project_id = (SELECT CAST(:project_id AS uuid)) AND trace_id = ANY(:trace_ids)
          AND kind = 'llm'
          AND request_hash IS NOT NULL
          AND attributes->>'spanlight.cache' IS DISTINCT FROM 'hit'
          AND NOT (attributes ? 'spanlight.fault.scenario')
          -- An SDK span over the gateway's record of the same call is a usage rollup of it
          -- (`app.ingest.usage_rollup`), not a retry.
          AND NOT usage_rollup
    ),
    superseded AS (
        SELECT trace_id,
               count(*)                    AS retry_calls,
               sum(cost_usd)               AS retry_cost_usd,
               bool_or(cost_usd IS NULL)   AS has_unpriced
        FROM ranked
        WHERE position > 1
        GROUP BY trace_id
    )
    UPDATE traces AS t
    SET span_count     = s.span_count,
        error_count    = s.error_count,
        input_tokens   = s.input_tokens,
        output_tokens  = s.output_tokens,
        cost_usd       = s.cost_usd,
        has_unpriced   = s.has_unpriced,
        started_at     = s.started_at,
        ended_at       = s.ended_at,
        retry_calls    = coalesce(r.retry_calls, 0),
        retry_cost_usd = CASE
            WHEN r.trace_id IS NULL THEN 0
            WHEN NOT r.has_unpriced AND r.retry_cost_usd < 1000000 THEN r.retry_cost_usd
        END
    FROM (
        SELECT trace_id,
               count(*)                                   AS span_count,
               count(*) FILTER (WHERE status = 'error')   AS error_count,
               -- A usage rollup repeats a descendant's usage: counted as a span, never summed.
               coalesce(sum(input_tokens) FILTER (WHERE NOT usage_rollup), 0)  AS input_tokens,
               coalesce(sum(output_tokens) FILTER (WHERE NOT usage_rollup), 0) AS output_tokens,
               -- traces.cost_usd is numeric(14, 8), like each span's cost. A total of a
               -- million dollars or more cannot be stored and is unknown (NULL), rather
               -- than an overflow that would fail every batch touching the trace.
               CASE WHEN sum(cost_usd) FILTER (WHERE NOT usage_rollup) < 1000000
                    THEN sum(cost_usd) FILTER (WHERE NOT usage_rollup) END AS cost_usd,
               -- A key-limit refusal (kind `gateway`) reached no provider: its cost is a
               -- known zero, not an unknown price.
               coalesce(
                   bool_or(
                       (kind = 'llm' OR model IS NOT NULL) AND cost_usd IS NULL
                       AND rate_limit_kind IS DISTINCT FROM 'gateway'
                   ) FILTER (WHERE NOT usage_rollup),
                   false
               )                                          AS has_unpriced,
               min(started_at)                            AS started_at,
               max(ended_at)                              AS ended_at
        FROM spans
        WHERE project_id = (SELECT CAST(:project_id AS uuid)) AND trace_id = ANY(:trace_ids)
        GROUP BY trace_id
    ) AS s
    LEFT JOIN superseded AS r ON r.trace_id = s.trace_id
    WHERE t.project_id = (SELECT CAST(:project_id AS uuid)) AND t.trace_id = s.trace_id
    """
)


async def _recompute_rollups(
    session: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> None:
    """Flag the traces' usage rollups, then total each trace from its stored spans."""
    await recompute_usage_rollups(session, project_id, trace_ids)
    await session.execute(
        _RECOMPUTE_ROLLUPS, {"project_id": project_id, "trace_ids": list(trace_ids)}
    )
