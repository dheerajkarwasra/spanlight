"""How one poll of a status feed ended: the vocabulary of `provider_status_sources.last_outcome`."""

import enum


class PollOutcome(enum.StrEnum):
    """The seven outcomes the source row's CHECK and the polls metric accept."""

    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
