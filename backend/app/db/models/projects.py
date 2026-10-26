"""Projects and their ingest API keys."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
    retention_days: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    capture_payloads: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # Whether ingestion also redacts emails and `+` phone numbers in span content (migration 1023).
    redact_contact_info: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Whether org members get the Monday summary email for this project (`weekly_digest` job).
    weekly_digest_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # Alert channels of the organization that hear about critical insights, at most 10. No
    # foreign key: a channel can be deleted while a project still names it, and notifying skips
    # an id that no longer resolves.
    insight_channel_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    # Metadata keys the daily attribution rollup keeps, at most 5, each matching the key pattern
    # (the CHECKs of migration 0801). Keys are identifiers, never values.
    promoted_metadata_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Values of `app.core.scopes.KeyScope`; the CHECK `api_keys_scopes_check` enforces the list.
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{ingest:write}'"))
    # NULL: the key does not expire. A key past this time is refused with 401 `KEY_EXPIRED`.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
