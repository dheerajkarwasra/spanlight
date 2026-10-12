"""The prompt registry: named templates of a project, their immutable versions and the labels
that point at them."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class PromptKind(enum.StrEnum):
    TEXT = "text"
    CHAT = "chat"


class Prompt(Base):
    """A named template in a project. Row-level security applies, as for `traces`.

    The name may contain `/` and is unique in the project. `updated_at` moves when a version is
    added or a label moves, so the list orders by recent activity. Every version shares the kind
    of the first one (`app.prompts.service` holds that).
    """

    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("project_id", "name", name="prompts_project_name_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptVersion(Base):
    """One version of a prompt, numbered from 1. Immutable once written.

    The database refuses any `UPDATE` (trigger `prompt_versions_immutable`, ADR 0014), apart from
    `created_by` becoming NULL when its author is deleted; an edit is always a new version.
    Row-level security applies through the parent prompt. `template` is
    `{"kind": "text", "text": ...}` or `{"kind": "chat", "messages": [{"role", "content"}]}`, and
    `kind` always equals `template["kind"]` (CHECK). `variables` is extracted by the server.
    `model_config` is free-form advice for clients (`model`, `temperature`, `max_tokens`,
    `top_p`), at most 2 KB.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="prompt_versions_prompt_version_key"),
        # The target of `prompt_labels`' composite key.
        UniqueConstraint("prompt_id", "id", name="prompt_versions_prompt_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    prompt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prompts.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[PromptKind] = mapped_column(_pg_enum(PromptKind, "prompt_kind"))
    template: Mapped[dict[str, Any]] = mapped_column(JSONB)
    variables: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    model_config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    commit_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptLabel(Base):
    """A movable name (`production`, `staging`, ...) for one version of a prompt.

    Row-level security applies through the parent prompt. `(prompt_id, version_id)` references
    `prompt_versions (prompt_id, id)`, so a label can only point at a version of its own prompt.
    `latest` is reserved (CHECK): it always means the highest version and is never stored.
    """

    __tablename__ = "prompt_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["prompt_id", "version_id"],
            ["prompt_versions.prompt_id", "prompt_versions.id"],
            ondelete="CASCADE",
            name="prompt_labels_version_fkey",
        ),
    )

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompts.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(Text, primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
