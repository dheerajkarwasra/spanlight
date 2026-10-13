"""Tell a project's insight channels about the critical insights a detector run just opened.

The engine calls `notify_opened` with `ApplyResult.opened`, in the transaction that opened the
insights, so the openings and their outbox rows commit or roll back together. Only a transition
into `open` is passed in, so a re-detection never notifies twice. A warning or info opening
notifies no one. Each insight becomes one `insight.opened` payload, fanned out through the alert
channels' `notify_channels` to every channel in `projects.insight_channel_ids`.
"""

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notify import notify_channels
from app.alerts.payload import (
    InsightBody,
    InsightEvidence,
    InsightPayload,
    OrgRef,
    ProjectRef,
    insight_url,
)
from app.core.ids import new_id
from app.db.models import Insight, Organization, Project
from app.insights.catalogue import KIND_INFO
from app.insights.schemas import InsightStatus, Severity

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)


async def notify_opened(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_ids: Sequence[uuid.UUID],
    *,
    settings: "Settings",
) -> int:
    """Queue an `insight.opened` notification for each critical insight in `insight_ids`.

    Returns how many outbox rows were queued. Insights that are not critical, no longer open or
    not the project's are left out; channel ids that no longer name a channel of the
    organization are skipped by `notify_channels`. The transaction must be bound to the project
    (row-level security on `insights`). Flushes, never commits.
    """
    if not insight_ids:
        return 0
    row = (
        await db.execute(
            select(Project, Organization)
            .join(Organization, Organization.id == Project.org_id)
            .where(Project.id == project_id)
        )
    ).first()
    if row is None:
        return 0
    project, org = row
    channel_ids = list(project.insight_channel_ids)
    if not channel_ids:
        return 0
    queued = 0
    for insight in await _critical_open(db, project_id, insight_ids):
        payload = _payload(insight, project, org, app_base_url=settings.app_base_url)
        if payload is None:
            continue
        rows = await notify_channels(db, channel_ids, payload, settings=settings)
        logger.info(
            "insight_notified",
            project_id=str(project_id),
            insight_id=str(insight.id),
            kind=insight.kind,
            rows=rows,
        )
        queued += rows
    await db.flush()
    return queued


async def _critical_open(
    db: AsyncSession, project_id: uuid.UUID, insight_ids: Sequence[uuid.UUID]
) -> list[Insight]:
    """The open critical insights among `insight_ids`, oldest opening first."""
    result = await db.scalars(
        select(Insight)
        .where(
            Insight.project_id == project_id,
            Insight.id.in_(list(dict.fromkeys(insight_ids))),
            Insight.severity == Severity.CRITICAL,
            Insight.status == InsightStatus.OPEN,
        )
        .order_by(Insight.last_seen_at, Insight.id)
    )
    return list(result)


def _payload(
    insight: Insight, project: Project, org: Organization, *, app_base_url: str
) -> InsightPayload | None:
    """The payload of one opened insight; None (logged) when its stored evidence does not fit.

    `occurred_at` is the detection that opened it (`last_seen_at`), so the payload needs no clock.
    """
    info = KIND_INFO.get(insight.kind)
    try:
        evidence = InsightEvidence.model_validate(insight.evidence)
    except ValidationError:
        logger.error("insight_evidence_invalid", insight_id=str(insight.id), kind=insight.kind)
        return None
    return InsightPayload(
        event_id=new_id(),
        occurred_at=insight.last_seen_at,
        org=OrgRef(id=org.id, name=org.name),
        project=ProjectRef(id=project.id, name=project.name),
        insight=InsightBody(
            id=insight.id,
            kind=insight.kind,
            label=info.label if info is not None else insight.kind,
            severity=insight.severity.value,
            fingerprint=insight.fingerprint,
            title=insight.title,
            summary=insight.summary,
            failure_layer=insight.failure_layer,
            suggested_fix=insight.suggested_fix,
            verification=insight.verification,
            occurrences=insight.occurrences,
            first_seen_at=insight.first_seen_at,
            last_seen_at=insight.last_seen_at,
            evidence=evidence,
        ),
        url=insight_url(app_base_url, org.id, project.id, insight.id),
    )
