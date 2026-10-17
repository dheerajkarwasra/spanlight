"""The providers' published status incidents and the polling state of each status feed.

Both tables are global: they describe the providers, not a tenant, so they have no tenant column
and no row-level security. Migration 0702 holds their CHECKs and seeds one source per provider.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class ProviderIncident(Base):
    """One incident from a provider's status feed, upserted on `(provider, external_id)`.

    `title` and `latest_update` are plain text (HTML stripped by the adapter); `api_affected`
    says whether the incident touches the API the gateway and SDK call. `feed_updated_at` is the
    feed's own last-modified time, None when the feed gives none.
    """

    __tablename__ = "provider_incidents"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="provider_incidents_provider_external_id_key"
        ),
        Index("provider_incidents_provider_started_idx", "provider", text("started_at DESC")),
        Index("provider_incidents_started_idx", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    api_affected: Mapped[bool] = mapped_column(Boolean)
    components: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(Text)
    latest_update: Mapped[str | None] = mapped_column(Text)
    feed_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderStatusSource(Base):
    """One provider's status feed: its fixed URL, conditional-request validators and last poll.

    `last_outcome` is one of `fetched`, `not_modified`, `http_error`, `parse_error`,
    `too_large`, `timeout` or `blocked`; `last_error_type` is an exception class name, never its
    message.
    """

    __tablename__ = "provider_status_sources"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error_type: Mapped[str | None] = mapped_column(Text)
