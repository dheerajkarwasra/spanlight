"""End-user analytics: the users of a project over a window and one user's detail.

Unknown values are `None`, never 0: `cost_usd` is `None` for a user whose calls were all unpriced.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.api.params import NO_NUL
from app.api.schemas.common import AggregateMetadata, Money, Page
from app.api.schemas.traces import SessionSummaryOut


class UserStats(BaseModel):
    """One end user over the days of the window that have any activity.

    `cost_usd` is the sum of the priced calls: `None` when calls were made and none was priced,
    and a lower bound while `unpriced_calls` (the calls without a price) is above 0. `tokens`
    adds what the calls reported: `None` when calls were made and none reported usage.
    `first_seen_day` and `last_seen_day` are the first and last of those days. `errors` counts
    failed LLM calls; the `error_count` of a session counts failed spans of every kind.
    """

    external_user_id: str
    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    cost_usd: Money | None
    tokens: int | None
    first_seen_day: date
    last_seen_day: date


class DayStats(BaseModel):
    """One UTC day of one user. A day without traces is all zeros with a `cost_usd` of 0; a day
    with calls but none priced has `cost_usd` `None`."""

    day: date
    traces: int | None
    llm_calls: int | None
    errors: int | None
    unpriced_calls: int | None
    cost_usd: Money | None
    tokens: int | None


class StatsWindow(BaseModel):
    """The window the figures cover: the requested one widened to whole UTC days."""

    start: datetime
    end: datetime


class UserListOut(Page[UserStats], AggregateMetadata):
    """`computed_at` is when the figures of the window were last recomputed (the latest of the
    days summed), `None` when no day of the window has been computed yet. `pending_traces` is
    how many user traces of the window (and environment) are not counted yet: they started after
    the latest recompute of their own UTC day, or their day has none yet, and more than 24 hours
    ago; the newer ones are read from the raw traces and counted. At most 10,000; 0 when none is
    pending, `None` only when the window has neither computed figures nor traces, or aggregate
    coverage is unavailable. `live` is true when the figures include every trace so far: the raw
    tail was merged and nothing is pending."""

    window: StatsWindow
    computed_at: datetime | None
    pending_traces: int | None
    live: bool


class UserDetailOut(AggregateMetadata):
    """`computed_at` is when the user's figures were last recomputed (the latest of the days
    summed), null when they all come from the raw tail. `live` says the tail was merged and the
    window has nothing pending, as on the list."""

    user: UserStats
    daily: list[DayStats]
    recent_sessions: list[SessionSummaryOut]
    window: StatsWindow
    computed_at: datetime | None
    live: bool


class EndUserErasureIn(BaseModel):
    """The end user to erase, by the `user_id` the application sent with its traces."""

    external_user_id: Annotated[str, Field(min_length=1, max_length=256, pattern=NO_NUL)]


class EndUserErasureOut(BaseModel):
    """The erasure is queued, not done: a worker job deletes the data in batches."""

    status: Literal["queued"]
    requested_at: datetime
