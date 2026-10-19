"""Request and response bodies for trace exports, and the row types the file writers take."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    model_validator,
)

from app.api.schemas.common import ApiModel
from app.api.window import MAX_WINDOW
from app.attribution.aggregate_sql import GroupKind
from app.db.models import ExportFormat, ExportKind, ExportStatus
from app.ingest.error_class import ErrorClass
from app.ingest.metadata import MetadataKey

FilterText = Annotated[str, StringConstraints(max_length=256)]


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AttributionExportFilters(BaseModel):
    """The window of an attribution export (required, at most 90 days) and its environment."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    environment: FilterText | None = None

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
        """The JSON kept in `exports.filters`; `model_validate` reads it back."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class ExportFilters(AttributionExportFilters):
    """The trace-list filters. `from` and `to` are required and at most 90 days apart."""

    release: FilterText | None = None
    model: FilterText | None = None
    status: Literal["ok", "error"] | None = None
    # Keeps traces with at least one span of this class, as in the trace list.
    error_class: ErrorClass | None = None
    user_id: FilterText | None = None
    session_id: FilterText | None = None
    tag: FilterText | None = None
    # Key and value keep the traces containing the pair; the key alone keeps those without it.
    metadata_key: MetadataKey | None = None
    metadata_value: FilterText | None = None
    q: FilterText | None = None

    @model_validator(mode="after")
    def _check_metadata_pair(self) -> Self:
        if self.metadata_value is not None and self.metadata_key is None:
            raise ValueError("metadata_value needs metadata_key")
        return self


class TraceExportIn(BaseModel):
    """One row per trace matching `filters`, as CSV or JSONL."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[ExportKind.TRACES] = ExportKind.TRACES
    format: ExportFormat
    filters: ExportFilters

    def stored(self) -> dict[str, Any]:
        """The JSON kept in `exports.filters`: the trace filters, as before Bet 3."""
        return self.filters.stored()


class AttributionExportIn(BaseModel):
    """The attribution table of the window, one row per value plus the `none` and `other`
    buckets, as CSV only. `key` is required with `group_by: metadata` and refused otherwise; a
    window over 24 hours reads the rollups, so its key must be promoted."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[ExportKind.ATTRIBUTION]
    format: Literal[ExportFormat.CSV]
    group_by: GroupKind
    key: MetadataKey | None = None
    filters: AttributionExportFilters

    def stored(self) -> dict[str, Any]:
        """The JSON kept in `exports.filters`: the request without its kind and format."""
        return {"group_by": self.group_by, "key": self.key, "filters": self.filters.stored()}


# The tag of the trace body is empty so its validation errors keep the field paths they had
# before `kind` existed (`format`, `filters.to`): a tag is a segment of the error location, and
# the error handler drops empty ones. An attribution error is `attribution.format`.
_TRACES_TAG = ""


def _kind(body: Any) -> str | None:
    """The discriminator; a body without `kind` is a trace export, as before Bet 3.

    None (a body that is not an object) makes pydantic report a validation error, not a crash.
    """
    value = (
        body.get("kind", ExportKind.TRACES)
        if isinstance(body, dict)
        else getattr(body, "kind", None)
    )
    if value is None:
        return None
    return _TRACES_TAG if value == ExportKind.TRACES else str(value)


ExportCreateIn = Annotated[
    Annotated[TraceExportIn, Tag(_TRACES_TAG)] | Annotated[AttributionExportIn, Tag("attribution")],
    Discriminator(_kind),
]


class ExportOut(ApiModel):
    id: uuid.UUID
    kind: ExportKind
    format: ExportFormat
    filters: ExportFilters | AttributionExportFilters
    group_by: GroupKind | None
    """Set on an attribution export, with `key` when the group is a metadata key."""
    key: str | None
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
    usage_rollup: bool
    """True when a descendant span carries its own usage; excluded from every total."""


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
    metadata: Mapping[str, str]
    spans: Sequence[SpanExportRow] = field(default=())
    # Too many spans or payload bytes for one batch: `spans` is empty and the job streams them.
    oversized: bool = False
