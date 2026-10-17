"""What the review policy service raises. The API maps each to a problem response
(`api/review_problems.py`)."""


class PolicyNotFoundError(Exception):
    """The project has no review policy."""


class QueueNotInProjectError(Exception):
    """The queue is not one of the project's."""


class QueueLacksFailureCategoryError(Exception):
    """The queue does not ask for the `failure_category` score config."""


class NothingSelectedError(Exception):
    """Every include is off and the random rate is zero: the policy would never queue a trace."""


class DayNotCompleteError(Exception):
    """The day is today or later; only a completed UTC day can be selected."""


class DayTooOldError(Exception):
    """The day is more than `MAX_MANUAL_DAYS_BACK` days back."""


class AlreadySelectedError(Exception):
    """A selection row exists for the project and day."""
