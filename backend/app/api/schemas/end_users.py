"""End-user analytics: the users of a project over a window and one user's detail.

Unknown values are `None`, never 0: `cost_usd` is `None` for a user whose calls were all unpriced.
"""

from datetime import date, datetime

from pydantic import BaseModel

from app.api.schemas.common import Money, Page
from app.api.schemas.traces import SessionSummaryOut


class UserStats(BaseModel):
    """One end user over the days of the window that have any activity.

    `cost_usd` is the sum of the priced calls: `None` when calls were made and none was priced,
    and a lower bound while `unpriced_calls` (the calls without a price) is above 0.
    `first_seen_day` and `last_seen_day` are the first and last of those days. `errors` counts
    failed LLM calls; the `error_count` of a session counts failed spans of every kind.
    """

    external_user_id: str
    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    cost_usd: Money | None
    tokens: int
    first_seen_day: date
    last_seen_day: date


class DayStats(BaseModel):
    """One UTC day of one user. A day without traces is all zeros with a `cost_usd` of 0; a day
    with calls but none priced has `cost_usd` `None`."""

    day: date
    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    cost_usd: Money | None
    tokens: int


class StatsWindow(BaseModel):
    """The window the figures cover: the requested one widened to whole UTC days."""

    start: datetime
    end: datetime


class UserListOut(Page[UserStats]):
    """`computed_at` is when the figures of the window were last recomputed (the latest of the
    days summed), `None` when no day of the window has been computed yet."""

    window: StatsWindow
    computed_at: datetime | None


class UserDetailOut(BaseModel):
    """`computed_at` is when the user's figures were last recomputed (the latest of the days
    summed)."""

    user: UserStats
    daily: list[DayStats]
    recent_sessions: list[SessionSummaryOut]
    window: StatsWindow
    computed_at: datetime | None
