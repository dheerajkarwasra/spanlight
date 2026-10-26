"""What the annotation services raise. The API maps each to a problem response
(`api/v1/annotations.py`)."""


class QueueNotFoundError(Exception):
    """No queue with this id in the project."""


class QueueItemNotFoundError(Exception):
    """No item with this id in the queue."""


class QueueNameTakenError(Exception):
    """Another queue of the project has this name."""


class QueueInUseError(Exception):
    """A review policy fills this queue; it cannot be deleted while one does."""


class UnknownScoreConfigError(Exception):
    """A score config id is not one of the project's."""


class UnknownTraceError(Exception):
    """Some trace ids are not traces of the project."""

    def __init__(self, trace_ids: list[str]) -> None:
        super().__init__(f"{len(trace_ids)} unknown traces")
        self.trace_ids = trace_ids


class ItemNotAssignedError(Exception):
    """The item is not claimed by the caller."""


class ItemNotPendingError(Exception):
    """The item is already done or skipped."""


class ScoreNotInQueueError(Exception):
    """A submitted score name is not one of the queue's configs."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("scores outside the queue's configs")
        self.names = names


class ScoreRequiredError(Exception):
    """A config of the queue has no score in the submission."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("scores missing")
        self.names = names


class ScoreRejectedError(Exception):
    """The write path rejected a submitted score; `reason` reads `<field>: <message>`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SpanNotInTraceError(Exception):
    """A score names a span that is not of the item's trace, or not the span-scoped item's."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("span not in the item's trace")
        self.names = names
