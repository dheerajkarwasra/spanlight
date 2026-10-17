"""The candidate statements of the nightly review selection, one per reason.

Every statement shares one predicate (`_CANDIDATE`): the project, the day window on `ended_at`
bounded on `started_at` through `traces_project_started_idx` (so a trace that ran longer than
`MAX_TRACE_DURATION` is not selected), the internal environments and Spanlight's own tags
excluded (even when a policy lists such an environment), the policy's environments when it
names any, at least one `llm` span, and no item of the trace in the target queue yet, whatever
its `span_id`. Each statement orders by the sampling hash (`_SAMPLE_POINT`, the expression of
`app.evals.sampler_queries` salted with the policy id) and stops at `:limit`, and carries the
pre-cap count in `total`. Every statement runs in a transaction bound to the project; the
`project_id` condition is the second line.
"""

from sqlalchemy import text

# The first 8 hex digits of sha256("<policy_id>:<trace_id>") as a number, like `sample_point`.
_SAMPLE_POINT = (
    "('x' || left(encode(sha256(convert_to(CAST(:hash_prefix AS text) || t.trace_id, 'UTF8')),"
    " 'hex'), 8))::bit(32)::bigint"
)

_CANDIDATE = """
      t.project_id = CAST(:project_id AS uuid)
      AND t.started_at >= :scan_from
      AND t.ended_at >= :day_start AND t.ended_at < :day_end
      AND (t.environment IS NULL OR t.environment <> ALL (CAST(:excluded AS text[])))
      AND NOT (t.tags && CAST(:excluded_tags AS text[]))
      AND NOT EXISTS (SELECT 1 FROM unnest(t.tags) AS tag WHERE tag LIKE :excluded_tag_prefix)
      AND (cardinality(CAST(:environments AS text[])) = 0
           OR t.environment = ANY (CAST(:environments AS text[])))
      AND EXISTS (
          SELECT 1 FROM spans s
          WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id AND s.kind = 'llm'
      )
"""

_NOT_QUEUED = """
      AND NOT EXISTS (
          SELECT 1 FROM annotation_queue_items i
          WHERE i.queue_id = CAST(:queue_id AS uuid) AND i.trace_id = t.trace_id
      )
"""

_TAIL = """
    ORDER BY sample_point, t.trace_id
    LIMIT :limit
"""

DOCTOR = text(
    f"""
    SELECT t.trace_id, {_SAMPLE_POINT} AS sample_point, i.id AS insight_id, i.kind,
           count(*) OVER () AS total
    FROM traces t
    JOIN LATERAL (
        SELECT i.id, i.kind
        FROM insights i
        WHERE i.project_id = t.project_id AND i.status = 'open'
          AND (i.evidence -> 'trace_ids') @> jsonb_build_array(t.trace_id)
        ORDER BY i.last_seen_at DESC, i.id DESC
        LIMIT 1
    ) i ON true
    WHERE {_CANDIDATE} {_NOT_QUEUED}
    {_TAIL}
    """
)

ERROR = text(
    f"""
    SELECT t.trace_id, {_SAMPLE_POINT} AS sample_point, t.error_count,
           f.span_id AS first_error_span_id, f.error_class,
           count(*) OVER () AS total
    FROM traces t
    LEFT JOIN LATERAL (
        SELECT s.span_id, s.error_class
        FROM spans s
        WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id AND s.status = 'error'
        ORDER BY s.started_at, s.span_id
        LIMIT 1
    ) f ON true
    WHERE {_CANDIDATE} {_NOT_QUEUED}
      AND t.error_count > 0
    {_TAIL}
    """
)

# The threshold is `percentile_disc(0.99)` of the known costs per trace name over the 7 UTC days
# ending with the selected day; a name with fewer than `:min_sample` known costs has none.
OUTLIER = text(
    f"""
    WITH thresholds AS (
        SELECT w.name,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY w.cost_usd) AS p99_usd,
               count(w.cost_usd) AS sample
        FROM traces w
        WHERE w.project_id = CAST(:project_id AS uuid)
          AND w.started_at >= :week_scan_from
          AND w.ended_at >= :week_start AND w.ended_at < :day_end
          AND w.name IS NOT NULL AND w.cost_usd IS NOT NULL
        GROUP BY w.name
        HAVING count(w.cost_usd) >= :min_sample
    )
    SELECT t.trace_id, {_SAMPLE_POINT} AS sample_point, t.cost_usd, th.p99_usd,
           t.name AS trace_name, th.sample,
           count(*) OVER () AS total
    FROM traces t
    JOIN thresholds th ON th.name = t.name
    WHERE {_CANDIDATE} {_NOT_QUEUED}
      AND t.cost_usd > th.p99_usd
    {_TAIL}
    """
)

RANDOM = text(
    f"""
    SELECT t.trace_id, {_SAMPLE_POINT} AS sample_point,
           count(*) OVER () AS total
    FROM traces t
    WHERE {_CANDIDATE} {_NOT_QUEUED}
      AND {_SAMPLE_POINT} < CAST(:threshold AS bigint)
    {_TAIL}
    """
)

# Traces of the day the queue already held, which the candidate statements left out.
ALREADY_QUEUED = text(
    f"""
    SELECT count(DISTINCT t.trace_id)
    FROM annotation_queue_items i
    JOIN traces t ON t.project_id = CAST(:project_id AS uuid) AND t.trace_id = i.trace_id
    WHERE i.queue_id = CAST(:queue_id AS uuid)
      AND {_CANDIDATE}
    """
)

INSERT_ITEMS = text(
    """
    INSERT INTO annotation_queue_items (id, queue_id, trace_id, reason, reason_detail, created_at)
    SELECT r.id, CAST(:queue_id AS uuid), r.trace_id, r.reason, CAST(r.detail AS jsonb),
           CAST(:now AS timestamptz)
    FROM unnest(CAST(:ids AS uuid[]), CAST(:trace_ids AS text[]), CAST(:reasons AS text[]),
                CAST(:details AS text[])) AS r (id, trace_id, reason, detail)
    ON CONFLICT (queue_id, trace_id, (coalesce(span_id, ''))) DO NOTHING
    RETURNING reason
    """
)
