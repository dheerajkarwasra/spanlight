"""Erasing one end user's data from one project: the request and the worker job that does it.

The request (`request_erasure`, called by `POST /projects/{id}/users/erasures`) queues an
`erase_end_user` job and writes an `end_user.erase_request` audit event in the caller's
transaction. The job deletes the user's traces in batches, each batch one transaction bound to
the project (row-level security filters every statement; no bypass, and every statement also
names the project), and loops until a batch is empty, which also catches spans that arrived
while it ran. Every step is "delete what still matches", so the queue's normal retries are safe;
the counts so far are kept on the job row, so the final event reports every attempt's work. When
the traces are gone it removes the derived rows that name the user and writes an
`end_user.erase` event with the counts per table; the last failed attempt writes the same event
with outcome `failure`.

A batch is bounded by spans, not traces: it takes traces in start order until their stored
`span_count` passes `SPAN_BATCH` (at most `TRACE_BATCH` traces), so one transaction never holds
more than a few thousand span rows. Spans go with their trace through the foreign key, in the
order ingest locks (trace, then spans), so a user who is still sending cannot deadlock the job.

Neither event holds the user's id: the audit log is append-only and hash-chained, so an id
written there could never be erased. Both carry `subject_hash` instead, a keyed hash an admin
can recompute from the id to check "was X erased?". The job payload holds the raw id until the
cleanup job removes finished jobs (seven days).

What stays, by design: anonymous hourly and daily rollups (no user dimension), user-scoped
budgets that name the user (configuration; their count is in the event), evaluator runs and
insight explanations (their costs feed budgets; the text that could hold the user's content is
nulled), the user's traces that carry no user id, and backups until they age out.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AuditAction, Job, JobStatus
from app.db.rls import bind_project
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, LeaseLostError, enqueue
from app.services.audit import Outcome, record_audit
from app.services.deletion import lock_org_for_write

logger = structlog.get_logger(__name__)

ERASE_JOB = "erase_end_user"
TRACE_BATCH = 1000
SPAN_BATCH = 5000
# The lease is 60 s; a batch of a few thousand spans is far shorter, but a tick keeps it alive
# while a slow statement runs, as the experiment runner does.
HEARTBEAT_SECONDS = 20.0
_HASH_DOMAIN = b"spanlight:end-user-erasure:v1\n"
_DROP_FINISHED_JOB = delete(Job).where(Job.status.in_([JobStatus.DONE, JobStatus.FAILED]))


def subject_hash(settings: Settings, project_id: uuid.UUID, external_user_id: str) -> str:
    """HMAC-SHA256 of the project and the user's id under `SECRET_KEY`, in hex.

    The message is `"spanlight:end-user-erasure:v1\\n<project id>:<external user id>"`: the
    domain prefix keeps the digest apart from any other use of the key, and the project id is a
    fixed-width UUID that cannot hold `:`, so two (project, id) pairs cannot share a message.
    Rotating `SECRET_KEY` changes the hash of every id.
    """
    message = _HASH_DOMAIN + f"{project_id}:{external_user_id}".encode()
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


async def request_erasure(
    db: AsyncSession,
    settings: Settings,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    external_user_id: str,
    actor_user_id: uuid.UUID,
    ip: str | None,
) -> datetime:
    """Queue the erasure and record the request; the caller has locked the project and commits.

    A second request while the job is queued or running is a no-op through the dedupe key; one
    after it finished runs again, which is harmless and catches data sent since. The finished
    job is removed first, since its key would block the new one for a week (`cleanup_sessions`),
    which also drops the raw id its payload holds.
    """
    digest = subject_hash(settings, project_id, external_user_id)
    dedupe_key = f"erase:{project_id}:{digest}"
    await db.execute(
        _DROP_FINISHED_JOB.where(Job.dedupe_key == dedupe_key).execution_options(
            synchronize_session=False
        )
    )
    await enqueue(
        db,
        ERASE_JOB,
        {
            "project_id": str(project_id),
            "org_id": str(org_id),
            "external_user_id": external_user_id,
        },
        dedupe_key=dedupe_key,
    )
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=AuditAction.END_USER_ERASE_REQUEST,
        target_type="end_user",
        target_id=digest,
        ip=ip,
        project_id=project_id,
    )
    return datetime.now(UTC)


# The oldest traces of the user up to the span cap: every trace that starts before the running
# span total reaches it, so the first always qualifies and a batch is never empty while any is left.
_USER_TRACES = text(
    """
    SELECT trace_id FROM (
        SELECT trace_id, span_count,
               sum(span_count) OVER (ORDER BY started_at, trace_id) AS running
        FROM traces
        WHERE project_id = :project_id AND external_user_id = :user_id
        ORDER BY started_at, trace_id
        LIMIT :trace_batch
    ) AS oldest
    WHERE running - span_count < :span_batch
    """
)
# Copies made from the user's traces: dataset items, the experiment items run over them and the
# traces those runs produced, the replays of the user's spans and the traces they produced.
_DATASET_ITEMS = text(
    """
    SELECT i.id FROM dataset_items i
    JOIN datasets d ON d.id = i.dataset_id
    WHERE d.project_id = :project_id AND i.source_trace_id = ANY(:trace_ids)
    """
)
_EXPERIMENT_TRACES = text(
    """
    SELECT i.trace_id FROM experiment_items i
    JOIN experiment_runs r ON r.id = i.run_id
    JOIN experiments e ON e.id = r.experiment_id
    WHERE e.project_id = :project_id
      AND i.dataset_item_id = ANY(:item_ids) AND i.trace_id IS NOT NULL
    """
)
_REPLAY_TRACES = text(
    """
    SELECT result_trace_id FROM replay_runs
    WHERE project_id = :project_id
      AND source_trace_id = ANY(:trace_ids) AND result_trace_id IS NOT NULL
    """
)
_SPANS = text(
    "SELECT count(*) FROM spans WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)"
)
# The copies' own tables, only when the batch has copies (their trace columns are not indexed).
_ITEM_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "experiment_items",
        """
        DELETE FROM experiment_items i
        USING experiment_runs r, experiments e
        WHERE r.id = i.run_id AND e.id = r.experiment_id AND e.project_id = :project_id
          AND i.dataset_item_id = ANY(:item_ids)
        """,
    ),
    (
        "dataset_items",
        """
        DELETE FROM dataset_items i
        USING datasets d
        WHERE d.id = i.dataset_id AND d.project_id = :project_id AND i.id = ANY(:item_ids)
        """,
    ),
)
# One statement per table, in foreign-key order; `:trace_ids` is the batch plus its copies.
# Spans go with their traces through the foreign key (see the module docstring).
_BATCH_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "replay_runs",
        "DELETE FROM replay_runs "
        "WHERE project_id = :project_id AND source_trace_id = ANY(:trace_ids)",
    ),
    ("scores", "DELETE FROM scores WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)"),
    (
        "annotation_queue_items",
        """
        DELETE FROM annotation_queue_items i
        USING annotation_queues q
        WHERE q.id = i.queue_id AND q.project_id = :project_id AND i.trace_id = ANY(:trace_ids)
        """,
    ),
    (
        "evaluator_runs_error_cleared",
        """
        UPDATE evaluator_runs r SET error = NULL
        FROM evaluators e
        WHERE e.id = r.evaluator_id AND e.project_id = :project_id
          AND r.trace_id = ANY(:trace_ids) AND r.error IS NOT NULL
        """,
    ),
    (
        "insight_explanations_text_cleared",
        """
        UPDATE insight_explanations SET text = NULL
        WHERE project_id = :project_id AND text IS NOT NULL AND insight_id IN (
            SELECT i.id FROM insights i
            WHERE i.project_id = :project_id AND EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(i.evidence -> 'trace_ids') AS cited
                WHERE cited = ANY(:trace_ids)
            )
        )
        """,
    ),
    ("traces", "DELETE FROM traces WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)"),
)
# Derived rows that name the user, once the traces are gone.
_USER_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "user_stats_daily",
        "DELETE FROM user_stats_daily "
        "WHERE project_id = :project_id AND external_user_id = :user_id",
    ),
    (
        "attribution_rollups_daily",
        "DELETE FROM attribution_rollups_daily "
        "WHERE project_id = :project_id AND group_kind = 'user' AND group_value = :user_id",
    ),
    (
        "alert_events",
        "DELETE FROM alert_events WHERE project_id = :project_id "
        "AND payload -> 'budget' ->> 'scope' = 'user' "
        "AND payload -> 'budget' ->> 'scope_id' = :user_id",
    ),
    (
        "exports_filter_stripped",
        "UPDATE exports SET filters = filters - 'user_id' "
        "WHERE project_id = :project_id AND filters ->> 'user_id' = :user_id",
    ),
)
_USER_BUDGETS = text(
    "SELECT count(*) FROM budgets "
    "WHERE project_id = :project_id AND scope = 'user' AND scope_id = :user_id"
)
# The counts so far ride on the job row, so a retry carries on from them (fenced like every other
# write to the job).
_SAVE_COUNTS = text(
    """
    UPDATE jobs SET payload = payload || jsonb_build_object('counts', CAST(:counts AS jsonb))
    WHERE id = :id AND fence = :fence AND status = 'running'
    """
)


def _add(counts: dict[str, int], table: str, result: Any) -> None:
    counts[table] = counts.get(table, 0) + int(getattr(result, "rowcount", 0))


async def _erase_batch(
    db: AsyncSession, project_id: uuid.UUID, user_id: str, counts: dict[str, int]
) -> bool:
    """Delete one batch of the user's traces with their copies; False when none is left."""
    scope = {"project_id": project_id, "user_id": user_id}
    rows = await db.execute(
        _USER_TRACES, {**scope, "trace_batch": TRACE_BATCH, "span_batch": SPAN_BATCH}
    )
    trace_ids = list(rows.scalars())
    if not trace_ids:
        return False
    params: dict[str, Any] = {**scope, "trace_ids": trace_ids}
    item_ids = list((await db.execute(_DATASET_ITEMS, params)).scalars())
    if item_ids:
        params["item_ids"] = item_ids
        params["trace_ids"] += list((await db.execute(_EXPERIMENT_TRACES, params)).scalars())
        for table, statement in _ITEM_STATEMENTS:
            _add(counts, table, await db.execute(text(statement), params))
    params["trace_ids"] += list((await db.execute(_REPLAY_TRACES, params)).scalars())
    counts["spans"] = counts.get("spans", 0) + int(await db.scalar(_SPANS, params) or 0)
    for table, statement in _BATCH_STATEMENTS:
        _add(counts, table, await db.execute(text(statement), params))
    return True


async def _save_counts(db: AsyncSession, job: ClaimedJob | None, counts: dict[str, int]) -> None:
    if job is not None:
        await db.execute(
            _SAVE_COUNTS, {"counts": json.dumps(counts), "id": job.id, "fence": job.fence}
        )


async def erase_end_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: uuid.UUID,
    external_user_id: str,
    counts: dict[str, int],
    job: ClaimedJob | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Erase the user's data batch by batch, adding to `counts` (per table) as it goes.

    With `job`, every batch saves the counts on the job row for the next attempt. `heartbeat`
    runs before each batch and stops the job (`LeaseLostError`) once another worker has it.
    """
    while True:
        if heartbeat is not None:
            await heartbeat()
        async with session_factory() as db:
            await bind_project(db, project_id)
            more = await _erase_batch(db, project_id, external_user_id, counts)
            await _save_counts(db, job, counts)
            await db.commit()
        if not more:
            break
    async with session_factory() as db:
        await bind_project(db, project_id)
        scope = {"project_id": project_id, "user_id": external_user_id}
        for table, statement in _USER_STATEMENTS:
            counts[table] = int(getattr(await db.execute(text(statement), scope), "rowcount", 0))
        counts["user_budgets_remaining"] = int(await db.scalar(_USER_BUDGETS, scope) or 0)
        await _save_counts(db, job, counts)
        await db.commit()


async def _tick(heartbeat: Callable[[], Awaitable[None]]) -> None:
    """Extend the lease every `HEARTBEAT_SECONDS` while a slow batch runs.

    A failed beat is retried next tick; a lost lease is left to the beat before the next batch,
    which raises into the job.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await heartbeat()
        except LeaseLostError:
            return
        except Exception as error:  # noqa: BLE001 - the next tick or batch beat tries again
            logger.warning("erase_heartbeat_failed", error_type=type(error).__name__)


async def _record_erased(
    context: TaskContext,
    payload: dict[str, Any],
    counts: dict[str, int],
    *,
    outcome: Outcome,
    reason: str | None,
) -> None:
    project_id = uuid.UUID(payload["project_id"])
    org_id = uuid.UUID(payload["org_id"])
    digest = subject_hash(context.settings, project_id, str(payload["external_user_id"]))
    async with context.session_factory() as db:
        # The organization may be gone (deleted while the job ran); then there is no log to write.
        if await lock_org_for_write(db, org_id):
            await record_audit(
                db,
                org_id=org_id,
                actor_user_id=None,
                action=AuditAction.END_USER_ERASE,
                target_type="end_user",
                target_id=digest,
                project_id=project_id,
                metadata=counts,
                outcome=outcome,
                reason=reason,
            )
            await db.commit()
    logger.info(
        "end_user_erased", project_id=str(project_id), subject=digest, outcome=outcome, **counts
    )


async def run_erase_end_user(context: TaskContext, payload: dict[str, Any]) -> None:
    counts: dict[str, int] = dict(payload.get("counts") or {})
    ticker = asyncio.create_task(_tick(context.heartbeat), name=f"erase-heartbeat-{context.job.id}")
    try:
        await erase_end_user(
            context.session_factory,
            project_id=uuid.UUID(payload["project_id"]),
            external_user_id=str(payload["external_user_id"]),
            counts=counts,
            job=context.job,
            heartbeat=context.heartbeat,
        )
    except LeaseLostError:
        raise  # another worker has the job; it writes the event
    except Exception as failure:
        if context.job.attempts >= context.job.max_attempts:
            await _record_erased(
                context, payload, counts, outcome="failure", reason=type(failure).__name__
            )
        raise
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
    await _record_erased(context, payload, counts, outcome="success", reason=None)
