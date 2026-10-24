"""Attribution: a project's spend and usage grouped by trace metadata, name, user or release.

Unknown values are `None`, never 0: `cost_usd` when no span of the group was priced, `retries`
and `retry_cost_usd` when the group's traces predate the retry columns or a retry could not be
priced. Money is a decimal string.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import Money
from app.attribution.aggregate_sql import GroupKind

AttributionSource = Literal["raw", "rollup"]


class AttributionTotalsOut(BaseModel):
    """The figures of every trace started in the window, whatever its group (the ledger the
    rows add up to).

    `traces` counts the traces; `llm_calls`, `errors` (status `error`) and `unpriced_calls` (no
    price) count their LLM calls; the token and cost sums take every span of the trace. `cost_usd`
    is a lower bound while `unpriced_calls` is above 0 and `None` when nothing was priced.
    """

    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Money | None
    retries: int | None
    retry_cost_usd: Money | None


class AttributionBucketOut(AttributionTotalsOut):
    """The traces without the dimension (`none`) or, on the rollup path, the values folded past
    the daily cap of 1,000 per day and environment (`other`, with how many values
    `folded_values` stands for; `None` on `none`)."""

    folded_values: int | None


class AttributionRowOut(AttributionTotalsOut):
    """One value of the dimension. `share_of_cost` is its cost over the window's total, rounded
    to four places; `None` when either is unknown."""

    value: str
    share_of_cost: float | None


class AttributionWindowOut(BaseModel):
    """The window the figures cover, in UTC: as requested on the raw path, widened to whole UTC
    days on the rollup path (`from` floored, `to` ceiled; `to` exclusive)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class AttributionOut(BaseModel):
    """A page of groups, with the `none` and `other` buckets and the totals on every page.

    `source` is `raw` (a window of 24 hours or less, read live from the traces: exact, any
    metadata key, no `other`) or `rollup` (a longer window, read from the daily rollup rows of
    promoted keys: exact per day, up to `updated_within_minutes` behind). `none` and `other`
    are `None` when they hold no traces.

    On the rollup source, `computed_at` is the latest recompute among the rows read (the last
    refresh pass; `None` when no row was read) and `pending_traces` counts the traces of the
    window and environment started after the latest recompute of their own UTC day, every trace
    of a day without rows, up to 10,000. Both are `None` on the raw source, where nothing is
    pending.
    """

    group_by: GroupKind
    key: str | None
    window: AttributionWindowOut
    source: AttributionSource
    updated_within_minutes: int
    computed_at: datetime | None
    pending_traces: int | None
    items: list[AttributionRowOut]
    next_cursor: str | None
    none: AttributionBucketOut | None
    other: AttributionBucketOut | None
    totals: AttributionTotalsOut


class SeriesPointOut(BaseModel):
    """One bucket of the series. `cost_usd` is `None` when its traces made calls and none was
    priced, a lower bound while `unpriced_calls` is above 0, and 0 for a bucket without traces."""

    bucket_start: datetime
    cost_usd: Money | None
    unpriced_calls: int


class AttributionSeriesOut(BaseModel):
    """The spend of the window per bucket, from the same source as the list: hourly on `raw` (a
    window of 24 hours or less), per UTC day on `rollup` (`window` widened to whole days). Every
    bucket of the window is present; the points add up to the list's `totals`, or to the group's
    row when `value` was given."""

    bucket: Literal["hour", "day"]
    source: AttributionSource
    window: AttributionWindowOut
    points: list[SeriesPointOut]
