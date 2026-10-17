"""What the billing fetch jobs decide without I/O: the window, the retry, the metric label.

`fetch_window` is the days a scheduled fetch covers; `retry_for` when an attempt that failed is
tried again, decided by the failure's `retry` kind and never by its status code; `outcome_label`
the `outcome` of `spanlight_billing_fetch_runs_total`; `parse_child_payload` the checked payload
of one source's fetch (`runs.child_payload` writes it).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.reconciliation.fetchers import FetchFailed

MAX_ATTEMPTS = 3
FIRST_FETCH_DAYS = 31
REFETCH_DAYS = 7
MAX_WINDOW_DAYS = 31
RATE_LIMIT_CAP = timedelta(minutes=15)
TRANSIENT_BACKOFF = (timedelta(minutes=5), timedelta(minutes=30))
_INVALID = {"invalid response", "unsupported currency", "too many pages", "response too large"}
_KEY_LINES = {"key cannot be decrypted", "CREDENTIALS_KEYS is not set"}


def fetch_window(now: datetime, *, first_fetch: bool) -> tuple[date, date]:
    """The days to fetch, both included: the week before today (UTC), a month on a first fetch."""
    today = now.astimezone(UTC).date()
    days = FIRST_FETCH_DAYS if first_fetch else REFETCH_DAYS
    return today - timedelta(days=days), today - timedelta(days=1)


def retry_for(error: FetchFailed, attempt: int) -> timedelta | None:
    """How long to wait before attempt `attempt + 1`, or None when retrying cannot help or the
    attempts are spent. Decided by the failure's `retry`, never by its status code."""
    if error.retry == "none" or attempt >= MAX_ATTEMPTS:
        return None
    if error.retry == "rate_limited":
        wanted = error.retry_after_s
        if wanted is None:
            return RATE_LIMIT_CAP
        return min(timedelta(seconds=wanted), RATE_LIMIT_CAP)
    return TRANSIENT_BACKOFF[max(attempt, 1) - 1]


def outcome_label(error: FetchFailed) -> str:
    """The `outcome` label of `spanlight_billing_fetch_runs_total` for a failed fetch."""
    if error.retry == "rate_limited":
        return "rate_limited"
    if error.retry == "transient":
        return "provider_error"
    line = error.status_line
    if line in _KEY_LINES or line.startswith(("401 ", "403 ")):
        return "auth_failed"
    return "invalid" if line in _INVALID else "provider_error"


@dataclass(frozen=True)
class ChildRequest:
    source_id: uuid.UUID
    day_from: date
    day_to: date
    trigger: str
    requested_by: uuid.UUID | None
    run_id: uuid.UUID | None


def parse_child_payload(payload: dict[str, Any]) -> ChildRequest:
    """The child's payload (`runs.child_payload`); `ValueError` when it is malformed."""
    try:
        request = ChildRequest(
            source_id=uuid.UUID(str(payload["source_id"])),
            day_from=date.fromisoformat(str(payload["day_from"])),
            day_to=date.fromisoformat(str(payload["day_to"])),
            trigger=str(payload["trigger"]),
            requested_by=_optional_id(payload.get("requested_by")),
            run_id=_optional_id(payload.get("run_id")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed fetch payload: {exc!r}") from exc
    days = (request.day_to - request.day_from).days + 1
    if request.trigger not in ("scheduled", "manual") or not 1 <= days <= MAX_WINDOW_DAYS:
        raise ValueError("malformed fetch payload: trigger or window out of range")
    return request


def _optional_id(value: object) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))
