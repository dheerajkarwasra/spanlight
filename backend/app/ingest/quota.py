"""Per-project ingest caps: a span rate and daily span and byte quotas, checked at ingest.

The instance settings `INGEST_SPANS_PER_SECOND`, `INGEST_SPANS_PER_DAY` and `INGEST_BYTES_PER_DAY`
(`0` = unlimited) apply to every project; the three override columns on `projects` replace them
for one project. The checks run in `app.api.ingest._store` after the body is parsed, so their
cost is the span count, and only for the two span ingest routes: the gateway recorder and the demo
job call the pipeline directly and are neither checked nor counted.

Order on a request (spec D2): bind the transaction to the project and share-lock its row (one
statement, so `ingest_quota_days` and `spans`, both under row-level security, become visible);
read today's usage in a second statement; refuse on `used >= limit` for spans, then bytes, unless
every span of the batch is already stored (a replay of an acknowledged batch, spec D6); then take
`len(spans)` tokens from the project's span bucket. A refused request writes nothing and counts its
spans in the process's loss counter under `quota_rate`, `quota_spans` or `quota_bytes`, which the
loss flush writes to `telemetry_losses_hourly`. The crossing batch is admitted: a request is never
refused because `used + n` would pass the limit, so a full batch is always admissible.

Usage is counted from what the write inserted or changed, as the last statement before commit,
for every project, capped or not (`add_usage`). The day is the UTC date of receipt on the app clock.
"""

import math
import time as clock_time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.observability import (
    INGEST_QUOTA_REJECTED_SPANS,
    INGEST_QUOTA_REJECTIONS,
    INGEST_QUOTA_UTILISATION,
    RATE_LIMIT_REJECTIONS,
)
from app.core.ratelimit import PostgresTokenBucket, take_token
from app.gateway.span_losses import loss_counter
from app.ingest.schemas import MAX_SPANS_PER_BATCH
from app.rollups.compute import floor_hour

logger = structlog.get_logger(__name__)

type DailyQuota = Literal["spans_per_day", "bytes_per_day"]

RATE_SCOPE = "ingest_project"
# A project at its cap refuses every request of a hot loop; the log says so once a minute.
_LOG_INTERVAL_SECONDS = 60.0
_last_log: dict[str, float] = {}


@dataclass(frozen=True)
class IngestCaps:
    """The effective caps of one project; `None` is unlimited."""

    spans_per_second: int | None
    spans_per_day: int | None
    bytes_per_day: int | None
    # Which of the three is the project's own override (`projects.ingest_*`) rather than the
    # instance setting; the quota endpoint reports it as the limit's `source`.
    overridden: frozenset[str] = frozenset()

    def source(self, name: str) -> Literal["project", "default"]:
        return "project" if name in self.overridden else "default"


@dataclass(frozen=True)
class Usage:
    spans: int
    bytes: int


@dataclass(frozen=True)
class BoundProject:
    """The project row the ingest route read while binding the transaction to it."""

    id: uuid.UUID
    capture_payloads: bool
    redact_contact_info: bool
    org_id: uuid.UUID
    caps: IngestCaps


class IngestRefusedError(Exception):
    """The request is over one of the project's caps; the route answers `429` from this."""

    def __init__(
        self,
        code: Literal["RATE_LIMITED", "INGEST_QUOTA_EXCEEDED"],
        detail: str,
        retry_after: int,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
        self.extensions = extensions


def effective_caps(
    settings: Settings,
    spans_per_second: int | None,
    spans_per_day: int | None,
    bytes_per_day: int | None,
) -> IngestCaps:
    """The project's overrides where set, else the instance settings; a `0` setting is unlimited."""
    overrides = {
        "spans_per_second": spans_per_second,
        "spans_per_day": spans_per_day,
        "bytes_per_day": bytes_per_day,
    }
    values = {
        name: override if override is not None else getattr(settings, f"ingest_{name}") or None
        for name, override in overrides.items()
    }
    return IngestCaps(
        spans_per_second=values["spans_per_second"],
        spans_per_day=values["spans_per_day"],
        bytes_per_day=values["bytes_per_day"],
        overridden=frozenset(name for name, value in overrides.items() if value is not None),
    )


def span_limiter(caps: IngestCaps) -> PostgresTokenBucket | None:
    """The project's span bucket: `spans_per_second` sustained, bursts of at least a full batch.

    The burst floor keeps `cost <= burst` for any batch (`PostgresTokenBucket.acquire` refuses a
    larger cost loudly), so a full 1,000-span batch is always admissible.
    """
    if caps.spans_per_second is None:
        return None
    rate = caps.spans_per_second
    return PostgresTokenBucket(rate=rate, burst=max(5 * MAX_SPANS_PER_BATCH, rate))


def exceeded(caps: IngestCaps, usage: Usage) -> tuple[DailyQuota, int, int] | None:
    """`(quota, limit, used)` for the first daily quota already used up, spans before bytes."""
    if caps.spans_per_day is not None and usage.spans >= caps.spans_per_day:
        return "spans_per_day", caps.spans_per_day, usage.spans
    if caps.bytes_per_day is not None and usage.bytes >= caps.bytes_per_day:
        return "bytes_per_day", caps.bytes_per_day, usage.bytes
    return None


def utilisation(caps: IngestCaps, usage: Usage) -> float | None:
    """`used / limit` of the daily quota closest to its cap; None without a daily cap."""
    ratios = [
        used / limit
        for used, limit in ((usage.spans, caps.spans_per_day), (usage.bytes, caps.bytes_per_day))
        if limit is not None
    ]
    return max(ratios) if ratios else None


def raw_span_pairs(raw_spans: list[Any]) -> list[tuple[str, str]] | None:
    """The batch's distinct `(trace_id, span_id)` pairs, lowercased as validation stores them.

    None when any span's ids are not both strings: such a span cannot be stored, so the batch is
    not a replay of stored data whatever the others are.
    """
    pairs: set[tuple[str, str]] = set()
    for raw in raw_spans:
        trace_id = raw.get("trace_id") if isinstance(raw, dict) else None
        span_id = raw.get("span_id") if isinstance(raw, dict) else None
        if not isinstance(trace_id, str) or not isinstance(span_id, str):
            return None
        pairs.add((trace_id.lower(), span_id.lower()))
    return sorted(pairs)


def utc_day(now: datetime) -> date:
    """The UTC date of `now`: the key of the day row, whatever zone the clock reports in."""
    return now.astimezone(UTC).date()


def next_midnight(now: datetime) -> datetime:
    """The next UTC midnight after `now` (the moment a daily quota resets)."""
    return datetime.combine(utc_day(now), time.min, UTC) + timedelta(days=1)


def seconds_until(moment: datetime, now: datetime) -> int:
    """Whole seconds from `now` to `moment` for `Retry-After`, rounded up and at least 1."""
    return max(1, math.ceil((moment - now).total_seconds()))


def describe_limit(quota: DailyQuota, limit: int) -> str:
    """`1,000,000 spans` or `5 GiB` (`512 MiB`, `4,096 bytes`) for the refusal's `detail`."""
    if quota == "spans_per_day":
        return f"{limit:,} spans"
    for unit, size in (("GiB", 1024**3), ("MiB", 1024**2)):
        if limit % size == 0:
            return f"{limit // size:,} {unit}"
    return f"{limit:,} bytes"


# The project row with its caps, share-locked and bound in one statement (what
# `app.ingest.pipeline._KEY_SHARE_PROJECT` does, extended by the columns the route needs). No join:
# Postgres applies a joined table's row-level security policy before this statement's
# `set_config` takes effect, so the usage row is read in a second statement.
_BIND_AND_LOCK = text(
    "SELECT id, capture_payloads, redact_contact_info, org_id, ingest_spans_per_second,"
    " ingest_spans_per_day, ingest_bytes_per_day,"
    " set_config('app.project_id', CAST(id AS text), true)"
    " FROM projects WHERE id = :project_id FOR KEY SHARE"
)
_READ_USAGE = text(
    "SELECT spans, bytes FROM ingest_quota_days WHERE project_id = :project_id AND day = :day"
)
# The replay probe: how many of the batch's pairs are stored, by primary key. The comment names
# it so a test can tell it from the pipeline's reads of `spans`.
_COUNT_STORED = text(
    """
    SELECT /* ingest quota replay probe */ count(*)
    FROM spans AS s
    JOIN unnest(CAST(:trace_ids AS text[]), CAST(:span_ids AS text[])) AS p (trace_id, span_id)
      ON s.trace_id = p.trace_id AND s.span_id = p.span_id
    WHERE s.project_id = :project_id
    """
)
_ADD_USAGE = text(
    """
    INSERT INTO ingest_quota_days (project_id, day, spans, bytes)
    VALUES (:project_id, :day, :spans, :bytes)
    ON CONFLICT (project_id, day) DO UPDATE
    SET spans = ingest_quota_days.spans + EXCLUDED.spans,
        bytes = ingest_quota_days.bytes + EXCLUDED.bytes
    """
)


async def bind_and_lock_project(
    db: AsyncSession, project_id: uuid.UUID, settings: Settings
) -> BoundProject | None:
    """Share-lock the project row, bind the transaction to it and read what ingest needs.

    None when the project no longer exists (deleted after the key was checked).
    """
    row = (await db.execute(_BIND_AND_LOCK, {"project_id": project_id})).one_or_none()
    if row is None:
        return None
    return BoundProject(
        id=row.id,
        capture_payloads=row.capture_payloads,
        redact_contact_info=row.redact_contact_info,
        org_id=row.org_id,
        caps=effective_caps(
            settings,
            row.ingest_spans_per_second,
            row.ingest_spans_per_day,
            row.ingest_bytes_per_day,
        ),
    )


async def read_usage(db: AsyncSession, project_id: uuid.UUID, day: datetime) -> Usage:
    """Today's admitted spans and bytes; zero without a row. Needs the project binding."""
    row = (
        await db.execute(_READ_USAGE, {"project_id": project_id, "day": utc_day(day)})
    ).one_or_none()
    return Usage(spans=0, bytes=0) if row is None else Usage(spans=row.spans, bytes=row.bytes)


async def add_usage(
    db: AsyncSession, project_id: uuid.UUID, day: datetime, *, spans: int, bytes_: int
) -> None:
    """Add the batch's inserted spans and body bytes to the project's day row."""
    await db.execute(
        _ADD_USAGE, {"project_id": project_id, "day": utc_day(day), "spans": spans, "bytes": bytes_}
    )


async def _all_stored(db: AsyncSession, project_id: uuid.UUID, raw_spans: list[Any]) -> bool:
    pairs = raw_span_pairs(raw_spans)
    if pairs is None:
        return False
    stored = await db.scalar(
        _COUNT_STORED,
        {
            "project_id": project_id,
            "trace_ids": [trace_id for trace_id, _ in pairs],
            "span_ids": [span_id for _, span_id in pairs],
        },
    )
    return int(stored or 0) == len(pairs)


def _log_once_a_minute(key: str) -> bool:
    now = clock_time.monotonic()
    last = _last_log.get(key)
    if last is not None and now - last < _LOG_INTERVAL_SECONDS:
        return False
    _last_log[key] = now
    return True


def refuse_daily(
    project_id: uuid.UUID, quota: DailyQuota, limit: int, used: int, spans: int, now: datetime
) -> IngestRefusedError:
    reason = "quota_spans" if quota == "spans_per_day" else "quota_bytes"
    loss_counter().add(project_id, "ingest", reason, floor_hour(now), spans)
    INGEST_QUOTA_REJECTIONS.labels(quota).inc()
    INGEST_QUOTA_REJECTED_SPANS.labels(quota).inc(spans)
    resets_at = next_midnight(now)
    retry_after = seconds_until(resets_at, now)
    if _log_once_a_minute(f"{project_id}:{quota}"):
        logger.warning(
            "ingest_quota_exceeded",
            project_id=str(project_id),
            quota=quota,
            used=used,
            limit=limit,
            retry_after=retry_after,
        )
    return IngestRefusedError(
        "INGEST_QUOTA_EXCEEDED",
        f"This project's daily ingest quota of {describe_limit(quota, limit)} is used up; "
        "it resets at 00:00 UTC.",
        retry_after,
        extensions={
            "quota": quota,
            "limit": limit,
            "used": used,
            "resets_at": resets_at.isoformat().replace("+00:00", "Z"),
        },
    )


async def check_caps(
    db: AsyncSession,
    rate_sessions: async_sessionmaker[AsyncSession],
    project: BoundProject,
    raw_spans: list[Any],
    now: datetime,
) -> tuple[DailyQuota, int, int] | None:
    """Refuse the batch or return the daily quota hit provisionally bypassed by a replay.

    `db` is the request's transaction, already bound to the project. The daily quotas are checked
    first, so an immediate daily refusal spends no rate tokens. A replay at the cap is debited
    and must be checked for changed spans after the pipeline; a later refusal keeps that debit.
    """
    caps = project.caps
    spans = len(raw_spans)
    hit = None
    if caps.spans_per_day is not None or caps.bytes_per_day is not None:
        usage = await read_usage(db, project.id, now)
        ratio = utilisation(caps, usage)
        if ratio is not None:
            INGEST_QUOTA_UTILISATION.observe(ratio)
        hit = exceeded(caps, usage)
        if hit is not None and not await _all_stored(db, project.id, raw_spans):
            raise refuse_daily(project.id, *hit, spans, now)
    limiter = span_limiter(caps)
    if limiter is None:
        return hit
    retry_after = await take_token(
        rate_sessions, limiter, f"ingest:project:{project.id}", scope=RATE_SCOPE, cost=spans
    )
    if retry_after is None:
        return hit
    RATE_LIMIT_REJECTIONS.labels(RATE_SCOPE).inc()
    loss_counter().add(project.id, "ingest", "quota_rate", floor_hour(now), spans)
    if _log_once_a_minute(f"{project.id}:rate"):
        logger.info("ingest_project_rate_limited", project_id=str(project.id), spans=spans)
    raise IngestRefusedError(
        "RATE_LIMITED",
        "Ingestion rate limit exceeded for this project.",
        max(1, math.ceil(retry_after)),
    )
