"""Telemetry losses: what a project lost on the way in, and each SDK exporter's own counters.

Both tables are under row-level security. Migration 0703 holds their CHECKs and unique keys.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class TelemetryLossHourly(Base):
    """Spans one project lost in one hour, by source (`gateway`, `ingest`, `sdk`) and reason.

    The loss flush upserts on `(project_id, bucket_start, source, reason)`, adding to `count`.
    """

    __tablename__ = "telemetry_losses_hourly"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "bucket_start", "source", "reason", name="telemetry_losses_hourly_key"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(BigInteger)


class SdkExporterCounter(Base):
    """The cumulative counters one SDK exporter process last reported for a project.

    `exporter_id` is the 32 lowercase hex characters the SDK draws once per process. Rows not
    seen for 7 days are pruned by the cleanup job.
    """

    __tablename__ = "sdk_exporter_counters"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    exporter_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dropped_total: Mapped[int] = mapped_column(BigInteger)
    failed_total: Mapped[int] = mapped_column(BigInteger)
    sdk: Mapped[str] = mapped_column(Text)
    sdk_version: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
