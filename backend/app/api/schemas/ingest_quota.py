"""Shape of `GET /projects/{project_id}/ingest-quota`: the project's effective ingest caps, today's
usage and the spans its caps refused per day.

Counts are clamped like the completeness answer's (`JsonCount`): a browser rounds past 2^53.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.completeness import JsonCount


class IngestLimitOut(BaseModel):
    value: int | None = Field(description="The effective cap; null when unlimited.")
    source: Literal["project", "default"] = Field(
        description=(
            "`project` for an override set on this project, `default` for the instance setting."
        )
    )


class IngestLimitsOut(BaseModel):
    spans_per_second: IngestLimitOut
    spans_per_day: IngestLimitOut
    bytes_per_day: IngestLimitOut


class IngestUsageTodayOut(BaseModel):
    day: date = Field(description="The current UTC day on the server clock.")
    spans: JsonCount = Field(description="Spans admitted today (new rows; replays count nothing).")
    bytes: JsonCount = Field(description="Decompressed request body bytes admitted today.")
    resets_at: datetime = Field(description="The next UTC midnight, when the daily quotas reset.")


class RefusedDayOut(BaseModel):
    day: date
    spans: JsonCount = Field(
        description="Spans in requests refused by any of the project's caps that day."
    )
    rate_spans: JsonCount = Field(
        description="Of those, spans refused by the span rate limit; clients usually resend them."
    )


class IngestQuotaOut(BaseModel):
    limits: IngestLimitsOut
    today: IngestUsageTodayOut
    rejected_days: list[RefusedDayOut] = Field(
        description="Days in the window with refused spans, ascending; empty when none."
    )
