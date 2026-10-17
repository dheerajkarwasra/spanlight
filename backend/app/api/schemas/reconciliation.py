"""The reconciliation report of one billing source (spec §8.3).

Every amount is a plain decimal string (`Money`), and `variance_ratio` one with four decimals.
An unknown figure is `null`, never `"0"`: a day no successful run covers has no provider side
(`provider_usd`, `residual_usd` and `variance_ratio` null), and a day whose rollups are still
being built has no Spanlight side (`spanlight_usd`, `explained_usd`, `residual_usd` and
`variance_ratio` null, `note` "rollup pending"). Two fields go beyond the spec's example: a
group's `note` (spec §13, "no provider line") and a day's `unattributed_calls` (calls whose span
named no provider, so no source can bill them).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.api.schemas.common import Money
from app.db.models.billing import BillingProvider, BillingSourceKind
from app.reconciliation.explained import ExplainedKind


class ReportSourceOut(BaseModel):
    id: uuid.UUID
    provider: BillingProvider
    kind: BillingSourceKind


class ReportWindowOut(BaseModel):
    """Whole UTC days: `start` is the first day's midnight, `end` the midnight after the last."""

    start: datetime
    end: datetime


class ReportProjectOut(BaseModel):
    id: uuid.UUID
    name: str


class ReportTokensOut(BaseModel):
    """Tokens by billing class; null when a count is unknown on that side."""

    uncached_input: int | None
    cache_read: int | None
    cache_write_5m: int | None
    cache_write_1h: int | None
    output: int | None


class ExplainedLineOut(BaseModel):
    """One known cause of a gap; `usd` is null when the cause has no computable amount."""

    kind: ExplainedKind
    usd: Money | None
    count: int | None
    tokens: int | None
    note: str


class ReportGroupOut(BaseModel):
    """One model group of one day: the seed or override pattern, or `unmapped:<name>`."""

    group: str
    model_pattern: str | None
    provider_models: list[str]
    spanlight_models: list[str]
    provider_usd: Money | None
    spanlight_usd: Money | None
    explained_usd: Money | None
    residual_usd: Money | None
    variance_ratio: Money | None
    provider_tokens: ReportTokensOut
    spanlight_tokens: ReportTokensOut
    explained: list[ExplainedLineOut]
    note: str | None


class ListedChargeOut(BaseModel):
    """A credit or a charge Spanlight cannot see: listed apart, never in the comparison."""

    description: str
    usd: Money


class ReportDayOut(BaseModel):
    day: date
    provisional: bool
    fetched_at: datetime | None
    incomplete: bool
    note: str | None
    provider_usd: Money | None
    spanlight_usd: Money | None
    explained_usd: Money | None
    residual_usd: Money | None
    variance_ratio: Money | None
    credits_usd: Money | None
    other_usd: Money | None
    unattributed_calls: int
    groups: list[ReportGroupOut]
    credits: list[ListedChargeOut]
    other: list[ListedChargeOut]


class ReportSummaryOut(BaseModel):
    """`settled_days` are the days no longer provisional; `days_within_2pct` and
    `max_abs_variance_ratio` read only settled, complete days (the ratio over groups the provider
    billed at least $0.01)."""

    days: int
    settled_days: int
    days_within_2pct: int
    max_abs_variance_ratio: Money | None


class ReconciliationOut(BaseModel):
    source: ReportSourceOut
    window: ReportWindowOut
    projects: list[ReportProjectOut]
    days: list[ReportDayOut]
    summary: ReportSummaryOut
