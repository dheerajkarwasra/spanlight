"""How a background job that ended `done` actually ended, and how a task asks to run again."""

import enum
from dataclasses import dataclass
from datetime import timedelta


class JobOutcome(enum.StrEnum):
    """What a task reports when it returns. A task that returns nothing means `OK`; a task that
    wants another attempt after a delay of its own returns `RetryAfter` instead.

    A job that skipped its work is still `done`: retrying cannot help, because the missing
    setting or the spent budget is still missing or spent on the next attempt.
    """

    OK = "ok"
    # An optional integration the task needs (object storage, a database URL, an API key) is off.
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    # The task would have spent money and a cap said no.
    SKIPPED_BUDGET = "skipped_budget"
    # The review selection found its project-day already selected and queued nothing.
    ALREADY_SELECTED = "already_selected"

    @property
    def is_skipped(self) -> bool:
        return self in (JobOutcome.SKIPPED_NOT_CONFIGURED, JobOutcome.SKIPPED_BUDGET)


@dataclass(frozen=True)
class RetryAfter:
    """Returned by a task whose attempt failed in a way a later attempt may fix, when the task
    knows how long to wait (a provider's `Retry-After`, its own backoff). The worker records the
    attempt as failed with `reason` and queues the job again `retry_after_s` from now, or fails
    it when this was the last attempt. `reason` is stored on the job: a status line, never a
    provider's response body."""

    retry_after_s: float
    reason: str

    @property
    def delay(self) -> timedelta:
        return timedelta(seconds=self.retry_after_s)
