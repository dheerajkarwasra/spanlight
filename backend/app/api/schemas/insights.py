"""The Doctor's insights: the problems detectors found, their evidence and the actions on them."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

from app.api.schemas.common import ApiModel, Money
from app.api.schemas.insight_feedback import FeedbackSummaryOut
from app.db.models import DetectorRun, Insight, InsightExplanation
from app.insights.catalogue import KIND_INFO
from app.insights.health_service import ProjectHealth
from app.insights.schemas import InsightStatus, Severity

__all__ = [
    "DetectorRunOut",
    "EvidenceOut",
    "EvidenceWindowOut",
    "ExplanationOut",
    "HealthComponentOut",
    "HealthOut",
    "InsightDetailOut",
    "InsightOut",
    "InsightSummaryOut",
    "MuteIn",
    "detector_run_out",
    "explanation_out",
    "health_out",
    "insight_out",
]

# A bound on the request's size only: the 1 to 500 character rule is the service's, so a bad
# reason is `422 INVALID_MUTE` like every other bad mute.
MuteReason = Annotated[str, StringConstraints(max_length=2000)]


class EvidenceWindowOut(BaseModel):
    start: datetime
    end: datetime


class EvidenceOut(BaseModel):
    """The traces and numbers behind an insight; decimals are strings, unknown values `None`."""

    trace_ids: list[str]
    metrics: dict[str, str | int | None]
    window: EvidenceWindowOut


class InsightOut(ApiModel):
    id: uuid.UUID
    kind: str
    # The catalogue's name for the kind (the kind itself when the catalogue no longer knows it).
    label: str
    severity: Severity
    status: InsightStatus
    title: str
    summary: str
    failure_layer: str
    certainty: str
    evidence: EvidenceOut
    suggested_fix: str
    verification: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int
    resolved_at: datetime | None
    muted_until: datetime | None
    mute_reason: str | None
    acknowledged_at: datetime | None


class ExplanationOut(ApiModel):
    """Claude's explanation of an insight: plain text, advisory, and what the call cost.

    `cost_usd` is the call's cost from its token usage (the reserved worst case when the answer
    reported no usage).
    """

    id: uuid.UUID
    model: str
    text: str
    cost_usd: Money | None
    created_by: uuid.UUID | None
    created_at: datetime


class InsightDetailOut(InsightOut):
    """The insight, its completed explanations (newest first) and the "Was this right?" votes."""

    explanations: list[ExplanationOut]
    feedback: FeedbackSummaryOut


class InsightSummaryOut(BaseModel):
    """Counts of the insights that still need a person: `open` and `acknowledged` ones."""

    open_critical: int
    open_warning: int
    open_info: int


class MuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # In the future and at most 90 days ahead (checked against the server's clock). A time
    # without an offset is rejected: it would be ambiguous.
    until: AwareDatetime
    reason: MuteReason


class HealthComponentOut(BaseModel):
    """One source of penalty. `observed` and `baseline` are decimal strings, `None` when unknown
    (or when the component has no baseline: findings and errors)."""

    name: str
    penalty: int
    observed: Money | None
    baseline: Money | None


class HealthOut(BaseModel):
    """The health score over the window; `value` is `None` (never 0) when it had no LLM calls.

    `approximate` is true when a percentile behind the score was estimated from rollup histograms.
    """

    value: int | None
    components: list[HealthComponentOut]
    open_critical: int
    open_warning: int
    window: EvidenceWindowOut
    approximate: bool


class DetectorRunOut(ApiModel):
    """One run of one detector. `findings` is `None` when the run failed (`error` is set)."""

    id: uuid.UUID
    detector: str
    window_start: datetime
    window_end: datetime
    findings: int | None
    truncated: bool
    duration_ms: int
    error: str | None
    ran_at: datetime


def insight_out(insight: Insight) -> InsightOut:
    info = KIND_INFO.get(insight.kind)
    return InsightOut(
        id=insight.id,
        kind=insight.kind,
        label=insight.kind if info is None else info.label,
        severity=insight.severity,
        status=insight.status,
        title=insight.title,
        summary=insight.summary,
        failure_layer=insight.failure_layer,
        certainty=insight.certainty,
        evidence=EvidenceOut.model_validate(insight.evidence),
        suggested_fix=insight.suggested_fix,
        verification=insight.verification,
        first_seen_at=insight.first_seen_at,
        last_seen_at=insight.last_seen_at,
        occurrences=insight.occurrences,
        resolved_at=insight.resolved_at,
        muted_until=insight.muted_until,
        mute_reason=insight.mute_reason,
        acknowledged_at=insight.acknowledged_at,
    )


def explanation_out(row: InsightExplanation) -> ExplanationOut:
    """A completed explanation (a reservation has no text and is never shown)."""
    if row.text is None:
        raise ValueError("a reservation is not an explanation")
    return ExplanationOut(
        id=row.id,
        model=row.model,
        text=row.text,
        cost_usd=row.cost_usd,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def detector_run_out(run: DetectorRun) -> DetectorRunOut:
    return DetectorRunOut.model_validate(run)


def health_out(health: ProjectHealth, start: datetime, end: datetime) -> HealthOut:
    score = health.score
    components = (
        []
        if score is None
        else [
            HealthComponentOut(
                name=component.name,
                penalty=component.penalty,
                observed=component.observed,
                baseline=component.baseline,
            )
            for component in score.components
        ]
    )
    return HealthOut(
        value=None if score is None else score.value,
        components=components,
        open_critical=health.open_critical,
        open_warning=health.open_warning,
        window=EvidenceWindowOut(start=start, end=end),
        approximate=health.approximate,
    )
