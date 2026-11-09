"""The health score of a project over a window: reads the inputs and hands them to `health`."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.insights import queries
from app.insights.health import HealthInputs, HealthScore, health_score
from app.insights.schemas import Severity
from app.metrics import service as metrics


@dataclass(frozen=True, slots=True)
class ProjectHealth:
    """The score (`None` when the window has no LLM calls) and what it was computed from."""

    score: HealthScore | None
    open_critical: int
    open_warning: int
    approximate: bool


def _decimal(value: float | Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


async def project_health(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> ProjectHealth:
    """Open counts include `open` and `acknowledged` insights; the baseline is the window before.

    The inputs are the Overview's own KPIs (same source, split and coverage rules as the tiles
    beside this one), so hours awaiting aggregate repair still count, as they do on those tiles.
    The alert-grade reader would read them as unknown until repair settles them. With no LLM call
    in the window there is nothing to judge, so there is no score (never 0).
    """
    counts = await queries.count_active_by_severity(db, project_id)
    open_critical = counts.get(Severity.CRITICAL, 0)
    open_warning = counts.get(Severity.WARNING, 0)
    kpis = await metrics.overview(db, project_id, window, environment)
    current, previous = kpis.current, kpis.previous
    if not current.llm_calls:
        return ProjectHealth(None, open_critical, open_warning, kpis.approximate)
    score = health_score(
        HealthInputs(
            open_critical=open_critical,
            open_warning=open_warning,
            error_rate=_decimal(current.error_rate),
            p95_ms=_decimal(current.p95_ms),
            p95_baseline_ms=_decimal(previous.p95_ms),
            spend_usd=_decimal(current.cost_usd),
            spend_baseline_usd=_decimal(previous.cost_usd),
        )
    )
    return ProjectHealth(score, open_critical, open_warning, kpis.approximate)
