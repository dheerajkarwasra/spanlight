"""The retention job's DELETE statements, one per table it prunes (`app.jobs.tasks.retention`).

Each deletes at most `:batch_size` rows of one project (`:project_id`) older than `:cutoff`, so
every transaction and its locks stay short on a large project; the job repeats a statement until
a batch comes back short.
"""

from datetime import timedelta

from sqlalchemy import text

# Small batches keep each transaction (and its locks) short on large projects.
DELETE_BATCH = text(
    """
    DELETE FROM traces
    WHERE (project_id, trace_id) IN (
        SELECT project_id, trace_id
        FROM traces
        WHERE project_id = :project_id AND started_at < :cutoff
        LIMIT :batch_size
    )
    """
)


# A resolved insight goes with the traces it cites; a detector run is only worth a week.
DELETE_RESOLVED_INSIGHTS = text(
    """
    DELETE FROM insights
    WHERE id IN (
        SELECT id
        FROM insights
        WHERE project_id = :project_id AND status = 'resolved' AND resolved_at < :cutoff
        LIMIT :batch_size
    )
    """
)
DELETE_DETECTOR_RUNS = text(
    """
    DELETE FROM detector_runs
    WHERE id IN (
        SELECT id
        FROM detector_runs
        WHERE project_id = :project_id AND ran_at < :cutoff
        LIMIT :batch_size
    )
    """
)
DETECTOR_RUN_RETENTION = timedelta(days=7)

# The per-user daily stats and the daily cost rollups are derived from the traces and spans, so
# they go with them: a day goes once it ends before the project's cutoff. User stats rows are
# picked by `ctid`: their natural key holds a nullable `environment`, which a tuple `IN` cannot
# match, and the key without it would take every environment's row, more than the batch.
DELETE_USER_STATS = text(
    """
    DELETE FROM user_stats_daily
    WHERE ctid = ANY(ARRAY(
        SELECT ctid
        FROM user_stats_daily
        WHERE project_id = :project_id
          AND day < (CAST(:cutoff AS timestamptz) AT TIME ZONE 'UTC')::date
        LIMIT :batch_size
    ))
    """
)
DELETE_COST_ROLLUPS = text(
    """
    DELETE FROM cost_rollups_daily
    WHERE id IN (
        SELECT id
        FROM cost_rollups_daily
        WHERE project_id = :project_id
          AND day < (CAST(:cutoff AS timestamptz) AT TIME ZONE 'UTC')::date
        LIMIT :batch_size
    )
    """
)


DELETE_SCORES = text(
    """
    DELETE FROM scores
    WHERE (project_id, id) IN (
        SELECT project_id, id
        FROM scores
        WHERE project_id = :project_id AND created_at < :cutoff
        LIMIT :batch_size
    )
    """
)
DELETE_ONLINE_EVALUATOR_RUNS = text(
    """
    DELETE FROM evaluator_runs
    WHERE id IN (
        SELECT r.id
        FROM evaluator_runs r
        JOIN evaluators e ON e.id = r.evaluator_id
        WHERE e.project_id = :project_id
          AND r.source = 'online'
          AND r.status NOT IN ('queued', 'running')
          AND r.started_at < :cutoff
        LIMIT :batch_size
    )
    """
)
DELETE_QUEUE_ITEMS = text(
    """
    DELETE FROM annotation_queue_items
    WHERE id IN (
        SELECT i.id
        FROM annotation_queue_items i
        JOIN annotation_queues q ON q.id = i.queue_id
        WHERE q.project_id = :project_id AND i.created_at < :cutoff
        LIMIT :batch_size
    )
    """
)

# The daily attribution rollups are derived data too: a day goes once it ends before the cutoff.
DELETE_ATTRIBUTION_ROLLUPS = text(
    """
    DELETE FROM attribution_rollups_daily
    WHERE id IN (
        SELECT id
        FROM attribution_rollups_daily
        WHERE project_id = :project_id
          AND day < (CAST(:cutoff AS timestamptz) AT TIME ZONE 'UTC')::date
        LIMIT :batch_size
    )
    """
)
