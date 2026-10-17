"""Retry totals per provider, from the figures ingestion stores on each trace.

``traces.retry_calls`` and ``traces.retry_cost_usd`` are computed by ingestion's trace recompute
(``app.ingest.store``), the only definition of a retry; this module only adds them up. A trace
has no provider, so each trace counts under one provider: the one that served most of its llm
calls (ties go to the provider whose name sorts first). That keeps the per-provider figures
summable into a window total; a trace that mixes providers is rare, and the note in the answer
says how it is attributed.

A trace is in the window when it started in it (``traces.started_at``), with the environment
filter on the trace. Only traces with retries are joined to their spans for attribution: a trace
with a known zero adds nothing, and a trace whose figures are unknown (``NULL``: stored before
the figures existed) is never attributed. Joining those would read the spans of every old trace
in a window of up to 90 days; instead one ``EXISTS`` on ``traces`` says whether any is in the
window, and then every provider's figures are unknown, since that trace could be any provider's.
A retried call with no known price makes its own provider's cost unknown.

The query runs in the caller's session and relies on its project binding for row-level
security; the explicit ``project_id`` filters are a second guard, not the only one.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow

_RETRY_TOTALS = text(
    """
    WITH windowed AS (
        SELECT trace_id, retry_calls, retry_cost_usd
        FROM traces
        WHERE project_id = :project_id
          AND started_at >= :start
          AND started_at < :end
          AND (:environment IS NULL OR environment = :environment)
          AND retry_calls > 0
    ),
    attributed AS (
        SELECT DISTINCT ON (s.trace_id) s.trace_id, s.provider
        FROM spans AS s
        JOIN windowed AS w ON w.trace_id = s.trace_id
        WHERE s.project_id = :project_id AND s.kind = 'llm'
        GROUP BY s.trace_id, s.provider
        ORDER BY s.trace_id, count(*) DESC, s.provider NULLS LAST
    )
    SELECT a.provider,
           sum(w.retry_calls)                      AS retry_calls,
           sum(w.retry_cost_usd)                   AS retry_cost_usd,
           bool_or(w.retry_cost_usd IS NULL)       AS cost_unknown
    FROM windowed AS w
    JOIN attributed AS a ON a.trace_id = w.trace_id
    GROUP BY a.provider
    """
).bindparams(bindparam("environment", type_=String))

_UNTRACKED = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM traces
        WHERE project_id = :project_id
          AND started_at >= :start
          AND started_at < :end
          AND (:environment IS NULL OR environment = :environment)
          AND retry_calls IS NULL
    )
    """
).bindparams(bindparam("environment", type_=String))


@dataclass(frozen=True, slots=True)
class RetryTotals:
    """One provider's retries in a window.

    ``retry_calls`` is ``None`` when any trace's count is unknown, and ``retry_cost_usd`` when
    any trace's cost is unknown (``cost_unknown``): never a partial sum that looks exact. A trace
    with an unknown count also has an unknown cost.
    """

    provider: str | None
    retry_calls: int | None
    retry_cost_usd: Decimal | None
    cost_unknown: bool


@dataclass(frozen=True, slots=True)
class RetryReport:
    """The retries of a window: the attributed totals, and whether an untracked trace (stored
    before retries were tracked) started in it."""

    by_provider: dict[str | None, RetryTotals]
    untracked: bool

    def for_provider(self, provider: str | None) -> RetryTotals:
        """The provider's totals: unknown when the window has an untracked trace, a known zero
        when none of its traces had retries."""
        if self.untracked:
            return RetryTotals(provider, retry_calls=None, retry_cost_usd=None, cost_unknown=True)
        found = self.by_provider.get(provider)
        if found is None:
            return RetryTotals(
                provider, retry_calls=0, retry_cost_usd=Decimal(0), cost_unknown=False
            )
        return found


async def read_retry_totals(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> RetryReport:
    """Retry totals of the traces that started in ``window``, by attributed provider."""
    params = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        "environment": environment,
    }
    rows = (await db.execute(_RETRY_TOTALS, params)).all()
    untracked = bool(await db.scalar(_UNTRACKED, params))
    by_provider = {
        row.provider: RetryTotals(
            provider=row.provider,
            retry_calls=int(row.retry_calls),
            retry_cost_usd=None if row.cost_unknown else row.retry_cost_usd,
            cost_unknown=bool(row.cost_unknown),
        )
        for row in rows
    }
    return RetryReport(by_provider=by_provider, untracked=untracked)
