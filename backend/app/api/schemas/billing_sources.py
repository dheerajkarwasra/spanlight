"""Billing sources (spec §8.2): the provider accounts an organization reconciles against.

The admin key is accepted in a create or rotate body as a `SecretStr`, so a repr or a log line
masks it, and no response model has a field for it: `key_set` and `key_id` say whether one is
sealed and under which master key. Its shape (trimmed, at most 512 characters, visible ASCII,
the kind's prefix) depends on the kind, so the service checks it, not the schema.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from app.api.schemas.common import UserOut
from app.db.models.billing import (
    BillingProvider,
    BillingSourceKind,
    FetchTrigger,
    RunStatus,
    SourceStatus,
)

SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ProviderScope = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
# Only stops an oversized body early; the service applies the real limit after trimming.
AdminKey = Annotated[SecretStr, Field(max_length=2048)]
# An organization has a handful of projects per account; the cap keeps a pasted list bounded.
ProjectIds = Annotated[list[uuid.UUID], Field(max_length=500)]


class BillingSourceCreate(BaseModel):
    """`provider` is required for `csv_upload` and derived for the API kinds; `admin_key` is
    required for the API kinds and refused for `csv_upload`."""

    model_config = ConfigDict(extra="forbid")

    kind: BillingSourceKind
    name: SourceName
    provider: BillingProvider | None = None
    admin_key: AdminKey | None = None
    provider_scope: ProviderScope | None = None
    project_ids: ProjectIds = []


class BillingSourceUpdate(BaseModel):
    """A partial edit: only the fields sent change. `project_ids` replaces the whole mapping;
    `provider_scope: null` clears the filter."""

    model_config = ConfigDict(extra="forbid")

    name: SourceName | None = None
    provider_scope: ProviderScope | None = None
    enabled: bool | None = None
    project_ids: ProjectIds | None = None

    @field_validator("name", "enabled", "project_ids")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        # Runs only for a value that was sent: an absent field keeps its default untouched.
        if value is None:
            raise ValueError("must not be null")
        return value


class BillingSourceRotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_key: AdminKey


class SourceProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class BillingSourceOut(BaseModel):
    """A billing source. Never the key: `key_set` says whether one is sealed."""

    id: uuid.UUID
    kind: BillingSourceKind
    provider: BillingProvider
    name: str
    provider_scope: str | None
    enabled: bool
    status: SourceStatus
    key_set: bool
    key_id: str | None
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    projects: list[SourceProjectOut]
    created_by: UserOut | None
    created_at: datetime
    updated_at: datetime


class RefreshAccepted(BaseModel):
    """A manual refresh was queued; `run_id` is the run it opened, listed under `/runs`."""

    run_id: uuid.UUID


class CsvUploadOut(BaseModel):
    """A CSV upload stored as one run: how many lines it wrote, and the UTC days they cover."""

    run_id: uuid.UUID
    lines_upserted: int
    days: list[date]


class BillingRunOut(BaseModel):
    """One fetch from the provider, or one CSV upload. `error` is an HTTP status line or a fixed
    short phrase, never a provider's response; `requested_by` is null for a scheduled fetch."""

    id: uuid.UUID
    trigger: FetchTrigger
    day_from: date
    day_to: date
    status: RunStatus
    error: str | None
    lines_upserted: int
    pages: int
    requested_by: UserOut | None
    started_at: datetime
    finished_at: datetime | None
