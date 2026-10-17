"""The `rollup_cost_daily_backfill` job's contract: its kind, payload, window and chunks (§10.4).

The request path queues backfills (the reconciliation report for days no pass built, a CSV upload
over a long span) and the worker runs them (`rollup_jobs`). Both import this module, which holds
no query, so the API never imports the job module and the row-level security bypass it can reach.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.end_users.days import ONE_DAY

BACKFILL_JOB = "rollup_cost_daily_backfill"
# The longest window one backfill recomputes: the report's own limit (spec §8.3).
MAX_BACKFILL_DAYS = 31
# A queued backfill's attempts (spec §10.4: max 3).
BACKFILL_ATTEMPTS = 3


def backfill_payload(project_id: uuid.UUID, day_from: date, day_to: date) -> dict[str, str]:
    """The payload of a backfill job for the days `day_from` to `day_to`, both included."""
    return {
        "project_id": str(project_id),
        "day_from": day_from.isoformat(),
        "day_to": day_to.isoformat(),
    }


def parse_backfill_payload(payload: Mapping[str, Any]) -> tuple[uuid.UUID, list[date]]:
    """The project and the days of a backfill payload; `ValueError` when it is malformed, the
    days run backwards, or the window is longer than `MAX_BACKFILL_DAYS`."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        day_from = date.fromisoformat(str(payload["day_from"]))
        day_to = date.fromisoformat(str(payload["day_to"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed backfill payload: {exc!r}") from exc
    count = (day_to - day_from).days + 1
    if count < 1 or count > MAX_BACKFILL_DAYS:
        raise ValueError(f"backfill window must be 1 to {MAX_BACKFILL_DAYS} days, got {count}")
    return project_id, [day_from + offset * ONE_DAY for offset in range(count)]


def day_chunks(days: Sequence[date]) -> list[tuple[date, date]]:
    """Ascending `days` grouped into windows of at most `MAX_BACKFILL_DAYS` days, each from its
    first to its last day: only the stretches the days fall in, not the gaps between them."""
    chunks: list[tuple[date, date]] = []
    for day in days:
        if chunks and (day - chunks[-1][0]).days < MAX_BACKFILL_DAYS:
            chunks[-1] = (chunks[-1][0], day)
        else:
            chunks.append((day, day))
    return chunks
