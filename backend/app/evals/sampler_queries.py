"""The sampler's reads and writes: sampling evaluators, candidate traces, and queued runs.

`sampling_evaluators` reads every project and needs row-level security bypassed; the others run
in a transaction bound to the evaluator's project.

`candidates` applies the rules of `app.evals.sampling` in SQL so only a short list leaves the
database: the time window, the exclusions, the evaluator's filters, the sampling hash, and no
online run yet for the trace. Each row carries the trace's last `llm` span (traces without one
never appear). Rows whose span has a stored payload come first, so spans a project did not store
cannot crowd out ones that can be evaluated.

The window is on `ended_at`, which has no index; the scan is bounded by `started_at` instead,
through `traces_project_started_idx`, so a trace that ran longer than `MAX_TRACE_DURATION` is not
sampled online.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evaluator
from app.evals.config import EvaluatorFilters
from app.evals.sampling import (
    EXCLUDED_ENVIRONMENTS,
    JUDGE_TAG,
    SpanFacts,
    TraceFacts,
    TraceWithSpans,
    sample_threshold,
)

MAX_TRACE_DURATION = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class SamplingEvaluator:
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    sampling_rate: Decimal
    filters: dict[str, Any]
    monthly_budget_usd: Decimal | None


async def sampling_evaluators(db: AsyncSession) -> list[SamplingEvaluator]:
    """Every enabled evaluator with a positive sampling rate, in every project."""
    rows = await db.execute(
        select(
            Evaluator.id,
            Evaluator.project_id,
            Evaluator.kind,
            Evaluator.sampling_rate,
            Evaluator.filters,
            Evaluator.monthly_budget_usd,
        )
        .where(Evaluator.enabled.is_(True), Evaluator.sampling_rate > 0)
        .order_by(Evaluator.id)
    )
    return [SamplingEvaluator(*row) for row in rows]


_CANDIDATES = text(
    """
    SELECT t.trace_id, t.name, t.environment, t.tags,
           EXTRACT(EPOCH FROM (t.ended_at - t.started_at)) * 1000 AS duration_ms,
           m.models,
           target.span_id, target.name AS span_name, target.model,
           target.started_at AS span_started_at, target.has_payload
    FROM traces t
    JOIN LATERAL (
        SELECT s.span_id, s.name, s.model, s.started_at,
               (s.input IS NOT NULL OR s.output IS NOT NULL) AS has_payload
        FROM spans s
        WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id AND s.kind = 'llm'
        ORDER BY s.started_at DESC, s.span_id DESC
        LIMIT 1
    ) target ON true
    CROSS JOIN LATERAL (
        SELECT coalesce(array_agg(DISTINCT s.model) FILTER (WHERE s.model IS NOT NULL),
                        CAST('{}' AS text[])) AS models
        FROM spans s
        WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id AND s.kind = 'llm'
    ) m
    WHERE t.project_id = CAST(:project_id AS uuid)
      AND t.started_at >= :scan_from
      AND t.ended_at BETWEEN :window_start AND :window_end
      AND (t.environment IS NULL OR t.environment <> ALL (CAST(:excluded AS text[])))
      AND NOT (CAST(:judge_tag AS text) = ANY (t.tags))
      AND ('x' || left(encode(sha256(convert_to(CAST(:hash_prefix AS text) || t.trace_id,
                                                'UTF8')), 'hex'), 8))::bit(32)::bigint
          < CAST(:threshold AS bigint)
      AND (cardinality(CAST(:environments AS text[])) = 0
           OR t.environment = ANY (CAST(:environments AS text[])))
      AND (cardinality(CAST(:trace_names AS text[])) = 0
           OR t.name = ANY (CAST(:trace_names AS text[])))
      AND (cardinality(CAST(:tags AS text[])) = 0 OR t.tags && CAST(:tags AS text[]))
      AND (cardinality(CAST(:models AS text[])) = 0 OR m.models && CAST(:models AS text[]))
      AND (CAST(:min_duration_ms AS double precision) IS NULL
           OR EXTRACT(EPOCH FROM (t.ended_at - t.started_at)) * 1000
              >= CAST(:min_duration_ms AS double precision))
      AND NOT EXISTS (
          SELECT 1 FROM evaluator_runs r
          WHERE r.evaluator_id = CAST(:evaluator_id AS uuid)
            AND r.trace_id = t.trace_id
            AND r.source = 'online'
      )
    ORDER BY target.has_payload DESC, t.ended_at, t.trace_id
    LIMIT :limit
    """
)


async def candidates(
    db: AsyncSession,
    evaluator: SamplingEvaluator,
    filters: EvaluatorFilters,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[TraceWithSpans]:
    """Traces the evaluator samples in the window and has not run on, each with its last `llm`
    span; at most `limit`, those with a stored payload first."""
    rows = await db.execute(
        _CANDIDATES,
        {
            "project_id": str(evaluator.project_id),
            "evaluator_id": str(evaluator.id),
            "scan_from": window_start - MAX_TRACE_DURATION,
            "window_start": window_start,
            "window_end": window_end,
            "excluded": sorted(EXCLUDED_ENVIRONMENTS),
            "judge_tag": JUDGE_TAG,
            "hash_prefix": f"{evaluator.id}:",
            "threshold": sample_threshold(evaluator.sampling_rate),
            "environments": list(filters.environments),
            "trace_names": list(filters.trace_names),
            "tags": list(filters.tags),
            "models": list(filters.models),
            "min_duration_ms": filters.min_duration_ms,
            "limit": limit,
        },
    )
    return [_candidate(row) for row in rows.mappings()]


def _candidate(row: Any) -> TraceWithSpans:
    trace = TraceFacts(
        trace_id=row["trace_id"],
        name=row["name"],
        environment=row["environment"],
        tags=tuple(row["tags"] or ()),
        duration_ms=None if row["duration_ms"] is None else float(row["duration_ms"]),
        models=frozenset(row["models"] or ()),
    )
    span = SpanFacts(
        span_id=row["span_id"],
        name=row["span_name"],
        model=row["model"],
        started_at=row["span_started_at"],
        has_payload=bool(row["has_payload"]),
    )
    return TraceWithSpans(trace, (span,))


_INSERT_RUNS = text(
    """
    INSERT INTO evaluator_runs (id, evaluator_id, trace_id, span_id, status, source, started_at)
    SELECT r.id, CAST(:evaluator_id AS uuid), r.trace_id, r.span_id, 'queued', 'online',
           CAST(:now AS timestamptz)
    FROM unnest(CAST(:ids AS uuid[]), CAST(:trace_ids AS text[]), CAST(:span_ids AS text[]))
         AS r (id, trace_id, span_id)
    ON CONFLICT (evaluator_id, trace_id, (coalesce(span_id, ''))) WHERE source = 'online'
    DO NOTHING
    RETURNING id
    """
)


async def insert_online_runs(
    db: AsyncSession,
    evaluator_id: uuid.UUID,
    targets: list[tuple[uuid.UUID, str, str]],
    now: datetime,
) -> list[uuid.UUID]:
    """Queue online runs for `(run_id, trace_id, span_id)` targets; a target that already has an
    online run is skipped. Returns the ids actually inserted."""
    if not targets:
        return []
    ids, trace_ids, span_ids = zip(*targets, strict=True)
    rows = await db.execute(
        _INSERT_RUNS,
        {
            "evaluator_id": str(evaluator_id),
            "now": now,
            "ids": list(ids),
            "trace_ids": list(trace_ids),
            "span_ids": list(span_ids),
        },
    )
    return [row[0] for row in rows]
