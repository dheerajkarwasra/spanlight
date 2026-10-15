"""Request and response bodies for trace exports, and the row types the file writers take."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.api.schemas.common import ApiModel
from app.api.window import MAX_WINDOW
from app.db.models import ExportFormat, ExportStatus
from app.ingest.error_class import ErrorClass

FilterText = Annotated[str, StringConstraints(max_length=256)]


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExportFilters(BaseModel):
    """The trace-list filters. `from` and `to` are required and at most 90 days apart."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    environment: FilterText | None = None
    release: FilterText | None = None
    model: FilterText | None = None
    status: Literal["ok", "error"] | None = None
    # Keeps traces with at least one span of this class, as in the trace list.
    error_class: ErrorClass | None = None
    user_id: FilterText | None = None
    session_id: FilterText | None = None
    tag: FilterText | None = None
    q: FilterText | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        # A time without an offset is UTC, as for every other datetime the API accepts.
        self.from_ = as_utc(self.from_)
        self.to = as_utc(self.to)
        if self.from_ >= self.to:
            raise ValueError("`from` must be earlier than `to`")
        if self.to - self.from_ > MAX_WINDOW:
            raise ValueError(f"the window may span at most {MAX_WINDOW // timedelta(days=1)} days")
        return self

    def stored(self) -> dict[str, Any]:
        """The JSON kept in `exports.filters`; `ExportFilters.model_validate` reads it back."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class ExportCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    filters: ExportFilters


class ExportOut(ApiModel):
    id: uuid.UUID
    format: ExportFormat
    filters: ExportFilters
    status: ExportStatus
    row_count: int | None
    size_bytes: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None
    download_url: str | None
    """A presigned link, valid for one hour; set only while the export is `done`."""


@dataclass(frozen=True, slots=True)
class SpanExportRow:
    """One stored span as an export carries it. `None` means unknown, never zero."""

    span_id: str
    parent_span_id: str | None
    kind: str
    name: str
    status: str
    status_message: str | None
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None
    cost_usd: Decimal | None
    pricing_version: str | None
    time_to_first_token_ms: float | None
    input: Any | None
    output: Any | None
    attributes: dict[str, Any]
    truncated: bool


@dataclass(frozen=True, slots=True)
class TraceExportRow:
    """One trace as an export carries it. `spans` is filled only when the format needs them."""

    trace_id: str
    name: str | None
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    status: Literal["ok", "error"]
    environment: str | None
    release: str | None
    user_id: str | None
    session_id: str | None
    span_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    has_unpriced: bool
    tags: Sequence[str]
    spans: Sequence[SpanExportRow] = field(default=())
