"""The ingest quota answer for one project: its caps, today's usage and refused spans by day."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.ingest_quota import (
    IngestLimitOut,
    IngestLimitsOut,
    IngestQuotaOut,
    IngestUsageTodayOut,
    RefusedDayOut,
)
from app.api.window import TimeWindow
from app.config import Settings
from app.db.models import Project
from app.ingest.quota import IngestCaps, effective_caps, next_midnight, read_usage, utc_day
from app.telemetry_losses.queries import read_quota_refusals


def project_caps(settings: Settings, project: Project) -> IngestCaps:
    return effective_caps(
        settings,
        project.ingest_spans_per_second,
        project.ingest_spans_per_day,
        project.ingest_bytes_per_day,
    )


def limits_out(caps: IngestCaps) -> IngestLimitsOut:
    def limit(name: str) -> IngestLimitOut:
        value: int | None = getattr(caps, name)
        return IngestLimitOut(value=value, source=caps.source(name))

    return IngestLimitsOut(
        spans_per_second=limit("spans_per_second"),
        spans_per_day=limit("spans_per_day"),
        bytes_per_day=limit("bytes_per_day"),
    )


async def ingest_quota_report(
    db: AsyncSession, project: Project, settings: Settings, window: TimeWindow, now: datetime
) -> IngestQuotaOut:
    """Runs in the caller's session, bound to the project (the usage row is under RLS)."""
    usage = await read_usage(db, project.id, now)
    refused = await read_quota_refusals(db, project.id, window)
    return IngestQuotaOut(
        limits=limits_out(project_caps(settings, project)),
        today=IngestUsageTodayOut(
            day=utc_day(now), spans=usage.spans, bytes=usage.bytes, resets_at=next_midnight(now)
        ),
        rejected_days=[
            RefusedDayOut(day=day.day, spans=day.spans, rate_spans=day.rate_spans)
            for day in refused
        ],
    )
