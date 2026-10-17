"""Writes of telemetry losses: ingest rejections, SDK counter deltas and flushed counts.

Every function runs in the caller's session and transaction, bound to the project
(`app.db.rls.bind_project`); the caller commits. Ingest calls them inside the batch's own
transaction, so a batch that fails records no loss, and the gateway's loss flush reuses
`upsert_losses` project by project.

The hour of a loss is the wall-clock hour it was recorded in (`floor_hour(now)`), not any span's
start: a lost span has no reliable time.
"""

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.schemas import ClientCountersIn, RejectedSpan
from app.rollups.compute import floor_hour
from app.telemetry_losses.reasons import reason_bucket

type LossEntries = Mapping[tuple[str, str, datetime], int]
"""Lost spans keyed by `(source, reason, bucket_start)`."""

# Adds to the hour's count, saturating at the largest bigint: an overflow would fail the whole
# transaction it runs in (an ingest batch, or a flush), and a count that large is already "all".
_UPSERT_LOSSES = text(
    """
    INSERT INTO telemetry_losses_hourly (project_id, bucket_start, source, reason, count)
    SELECT CAST(:project_id AS uuid), e.bucket_start, e.source, e.reason, e.count
    FROM unnest(
        CAST(:bucket_starts AS timestamptz[]),
        CAST(:sources AS text[]),
        CAST(:reasons AS text[]),
        CAST(:counts AS bigint[])
    ) AS e (bucket_start, source, reason, count)
    ON CONFLICT (project_id, bucket_start, source, reason) DO UPDATE
    SET count = LEAST(
        telemetry_losses_hourly.count::numeric + EXCLUDED.count, 9223372036854775807
    )::bigint
    """
)

# One statement for one exporter's report. `previous` locks the exporter's row; when it exists the
# row keeps the larger of each stored and reported total, and the losses are the increases. When
# it does not, the report is inserted and counted whole, a first sighting. If another transaction
# inserted the row after this statement's snapshot, the insert does nothing and nothing is
# counted: its writer counted that report, and the exporter's next one counts any growth.
_RECORD_SDK_COUNTERS = text(
    """
    WITH previous AS (
        SELECT dropped_total, failed_total
        FROM sdk_exporter_counters
        WHERE project_id = CAST(:project_id AS uuid) AND exporter_id = :exporter_id
        FOR UPDATE
    ),
    updated AS (
        UPDATE sdk_exporter_counters AS c
        SET dropped_total = greatest(c.dropped_total, CAST(:dropped AS bigint)),
            failed_total = greatest(c.failed_total, CAST(:failed AS bigint)),
            sdk = :sdk,
            sdk_version = :sdk_version,
            last_seen_at = :now
        WHERE c.project_id = CAST(:project_id AS uuid) AND c.exporter_id = :exporter_id
          AND EXISTS (SELECT FROM previous)
    ),
    inserted AS (
        INSERT INTO sdk_exporter_counters
            (project_id, exporter_id, dropped_total, failed_total, sdk, sdk_version, last_seen_at)
        SELECT CAST(:project_id AS uuid), :exporter_id, CAST(:dropped AS bigint),
               CAST(:failed AS bigint), :sdk, :sdk_version, :now
        WHERE NOT EXISTS (SELECT FROM previous)
        ON CONFLICT (project_id, exporter_id) DO NOTHING
        RETURNING dropped_total, failed_total
    ),
    deltas (reason, n) AS (
        SELECT 'dropped', greatest(0, CAST(:dropped AS bigint) - p.dropped_total) FROM previous p
        UNION ALL
        SELECT 'failed', greatest(0, CAST(:failed AS bigint) - p.failed_total) FROM previous p
        UNION ALL
        SELECT 'dropped', i.dropped_total FROM inserted i
        UNION ALL
        SELECT 'failed', i.failed_total FROM inserted i
    )
    INSERT INTO telemetry_losses_hourly (project_id, bucket_start, source, reason, count)
    SELECT CAST(:project_id AS uuid), :bucket_start, 'sdk', d.reason, d.n
    FROM deltas d
    WHERE d.n > 0
    ON CONFLICT (project_id, bucket_start, source, reason) DO UPDATE
    SET count = LEAST(
        telemetry_losses_hourly.count::numeric + EXCLUDED.count, 9223372036854775807
    )::bigint
    """
)


async def upsert_losses(db: AsyncSession, project_id: uuid.UUID, entries: LossEntries) -> None:
    """Add `entries` to the project's hourly losses in one statement; counts below 1 are skipped.

    Rows are written in key order, so two writers of the same hours cannot deadlock.
    """
    rows = sorted((bucket, source, reason, n) for (source, reason, bucket), n in entries.items())
    rows = [row for row in rows if row[3] > 0]
    if not rows:
        return
    await db.execute(
        _UPSERT_LOSSES,
        {
            "project_id": project_id,
            "bucket_starts": [row[0] for row in rows],
            "sources": [row[1] for row in rows],
            "reasons": [row[2] for row in rows],
            "counts": [row[3] for row in rows],
        },
    )


async def record_ingest_rejections(
    db: AsyncSession, project_id: uuid.UUID, rejected: Sequence[RejectedSpan], now: datetime
) -> None:
    """Count a batch's rejected spans as `ingest` losses, by reason bucket, in the hour of `now`."""
    hour = floor_hour(now)
    by_reason = Counter(reason_bucket(span.reason) for span in rejected)
    await upsert_losses(
        db, project_id, {("ingest", reason, hour): n for reason, n in by_reason.items()}
    )


async def record_sdk_counters(
    db: AsyncSession, project_id: uuid.UUID, counters: ClientCountersIn, now: datetime
) -> None:
    """Turn one exporter's cumulative totals into `sdk` losses (the increase since its last report).

    A total lower than the stored one (a late, older report) writes no loss and keeps the stored
    total; an equal report only advances `last_seen_at`.
    """
    await db.execute(
        _RECORD_SDK_COUNTERS,
        {
            "project_id": project_id,
            "exporter_id": counters.exporter_id,
            "dropped": counters.dropped_total,
            "failed": counters.failed_total,
            "sdk": counters.sdk,
            "sdk_version": counters.version,
            "now": now,
            "bucket_start": floor_hour(now),
        },
    )


def check_client(raw: Any) -> tuple[ClientCountersIn | None, list[str]]:
    """The batch's `client` counters, and the fields that made an invalid one be ignored.

    Absent: `(None, [])`. Invalid: `(None, <failing field locations>)`, names only, never the
    values, which are the client's; the caller logs them with the batch.
    """
    if raw is None:
        return None, []
    try:
        return ClientCountersIn.model_validate(raw), []
    except ValidationError as exc:
        fields = {
            ".".join(str(part) for part in error["loc"]) or "client" for error in exc.errors()
        }
        return None, sorted(fields)
