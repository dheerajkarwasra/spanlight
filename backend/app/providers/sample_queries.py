"""Sample failures for the provider health page: the newest failed llm calls, one page at a time.

A sample is a span matching ``FAILED_LLM_SPAN`` (fault injections from the gateway's lab left
out, as every count on the page leaves them out). Pages are newest first by
``(started_at, span_id, trace_id)``; a span id is unique only within its trace, so the trace id
breaks the last tie. The span's provider fields are columns; the retry-after, attempt count and
request id are read from its attributes as text and parsed by the caller, because attribute
values are whatever the client sent.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.rollups.error_compute import FAILED_LLM_SPAN

_CONDITIONS = {
    "environment": "t.environment = :environment",
    "provider": "s.provider = :provider",
    "error_class": "s.error_class = :error_class",
    "rate_limit_kind": "s.rate_limit_kind = :rate_limit_kind",
    "after": "(s.started_at, s.span_id, s.trace_id) < (:after_started, :after_span, :after_trace)",
}
_TRACE_JOIN = "JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id"


@dataclass(frozen=True, slots=True)
class SampleFilters:
    """Which failures to sample; ``None`` means any value."""

    environment: str | None = None
    provider: str | None = None
    error_class: str | None = None
    rate_limit_kind: str | None = None


@dataclass(frozen=True, slots=True)
class SampleCursor:
    """The sort key of the last sample of the previous page."""

    started_at: datetime
    span_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class SampleRow:
    trace_id: str
    span_id: str
    started_at: datetime
    model: str | None
    status_message: str | None
    error_class: str
    rate_limit_kind: str | None
    provider_error_type: str | None
    provider_error_code: str | None
    gateway_retry_after: str | None
    provider_retry_after: str | None
    attempts: str | None
    request_id: str | None


@cache
def _statement(filters: frozenset[str]) -> TextClause:
    join = _TRACE_JOIN if "environment" in filters else ""
    conditions = "".join(f"\n      AND {_CONDITIONS[name]}" for name in sorted(filters))
    return text(
        f"""
        SELECT s.trace_id, s.span_id, s.started_at, s.model, s.status_message, s.error_class,
               s.rate_limit_kind, s.provider_error_type, s.provider_error_code,
               s.attributes ->> 'spanlight.gateway.retry_after_s' AS gateway_retry_after,
               s.attributes ->> 'provider.retry_after_s'          AS provider_retry_after,
               s.attributes ->> 'spanlight.gateway.attempts'      AS attempts,
               s.attributes ->> 'provider.request_id'             AS request_id
        FROM spans AS s
        {join}
        WHERE s.project_id = :project_id
          AND s.started_at >= :start
          AND s.started_at < :end
          AND {FAILED_LLM_SPAN}{conditions}
        ORDER BY s.started_at DESC, s.span_id DESC, s.trace_id DESC
        LIMIT :limit
        """
    )


async def read_samples(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    filters: SampleFilters,
    *,
    limit: int,
    after: SampleCursor | None,
) -> list[SampleRow]:
    """Up to ``limit`` failures in ``window`` after the cursor, newest first."""
    values = {
        "environment": filters.environment,
        "provider": filters.provider,
        "error_class": filters.error_class,
        "rate_limit_kind": filters.rate_limit_kind,
    }
    params: dict[str, Any] = {name: value for name, value in values.items() if value is not None}
    conditions = set(params)
    if after is not None:
        conditions.add("after")
        params |= {
            "after_started": after.started_at,
            "after_span": after.span_id,
            "after_trace": after.trace_id,
        }
    params |= {"project_id": project_id, "start": window.start, "end": window.end, "limit": limit}
    rows: Sequence[Any] = (await db.execute(_statement(frozenset(conditions)), params)).all()
    return [SampleRow(**row._mapping) for row in rows]
