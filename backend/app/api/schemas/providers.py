"""Shapes of the provider health routes and the provider incident list.

Unknown values are `null`, never 0: an error rate without calls, a retry cost when any retried
call has no known price. Rates and money are decimal strings.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.common import ApiModel, Money, Page


class WindowOut(BaseModel):
    start: datetime
    end: datetime


class ErrorClassCountOut(BaseModel):
    error_class: str
    count: int


class RateLimitKindCountOut(BaseModel):
    kind: str
    count: int
    with_retry_after: int = Field(
        description="How many of these refusals carried a `retry-after`: the provider's, or for "
        "the `gateway` kind the one the gateway's key limit told the client."
    )


class IncidentRefOut(ApiModel):
    """A provider status page incident that overlapped the window and touched the API."""

    id: uuid.UUID
    title: str
    impact: str
    status: str
    started_at: datetime
    resolved_at: datetime | None
    url: str


class ProviderHealthOut(BaseModel):
    provider: str | None = Field(description="Null for llm calls whose provider is unknown.")
    llm_calls: int
    errors: int = Field(description="Failed llm calls, gateway lab fault injections left out.")
    error_rate: str | None = Field(description="`errors / llm_calls`; null without calls.")
    by_class: list[ErrorClassCountOut] = Field(
        description="Classes with failures, in the page's fixed order."
    )
    rate_limit_kinds: list[RateLimitKindCountOut]
    retry_calls: int | None = Field(
        description="Retried calls; null when any trace's count is unknown (stored before "
        "retries were tracked)."
    )
    retry_cost_usd: Money | None = Field(
        description="Null when any trace's retry cost is unknown (`retry_cost_unknown`)."
    )
    retry_cost_unknown: bool
    incidents: list[IncidentRefOut]


class HealthReportOut(BaseModel):
    window: WindowOut
    approximate: bool = Field(
        description="Whole hours were read from the hourly rollups, as of their last run."
    )
    providers: list[ProviderHealthOut] = Field(description="Busiest first.")
    retry_cost_note: str


class ErrorBucketOut(BaseModel):
    bucket_start: datetime
    by_class: dict[str, int] = Field(description="Failed llm calls per class; zero classes absent.")
    approximate: bool = Field(description="The bucket holds an hour read from the hourly rollup.")


class ErrorSampleOut(BaseModel):
    trace_id: str
    span_id: str
    started_at: datetime
    model: str | None
    status_message: str | None
    error_class: str
    rate_limit_kind: str | None
    provider_error_type: str | None
    provider_error_code: str | None
    retry_after_s: float | None
    attempts: int | None = Field(description="Gateway attempts behind the call; null elsewhere.")
    request_id: str | None


class SamplesPage(Page[ErrorSampleOut]):
    clamped: bool = Field(description="The window was longer than 24 h and cut to its last 24 h.")


class IncidentOut(ApiModel):
    id: uuid.UUID
    provider: str
    title: str
    impact: str
    status: str
    api_affected: bool
    components: list[str]
    started_at: datetime
    resolved_at: datetime | None
    url: str
    latest_update: str | None


class StatusSourceOut(ApiModel):
    provider: str
    last_success_at: datetime | None
    last_polled_at: datetime | None
    last_outcome: str | None
    consecutive_failures: int


class IncidentListOut(BaseModel):
    polling: Literal["enabled", "disabled"]
    sources: list[StatusSourceOut]
    items: list[IncidentOut]
    next_cursor: str | None
