"""The statements that rebuild ``error_rollups_hourly`` for one project and hour range.

``app.rollups.compute.compute_rollups`` runs them in its own transaction, after the span and
trace rollups, under the same project binding, advisory lock and range, delete-then-insert for
the reason given there: a span sent again with another model, class or kind moves to another
group, and an upsert would leave the old group's row behind.

A row counts failed ``llm`` spans (``status = 'error'`` with an error class) of one hour by the
trace's environment and the span's provider, model, error class and rate-limit kind. Unknown
dimensions stay NULL. Spans with ``spanlight.fault.scenario`` are fault injections from the
gateway's lab, not provider failures, and are left out, as the detectors leave them out.
``with_retry_after`` counts the failed spans that carried a provider ``retry-after``, from the
gateway (``spanlight.gateway.retry_after_s``) or from the provider response fields
(``provider.retry_after_s``), as a number: the gateway stores the key as null when there was no
``Retry-After``, so the key alone says nothing. ``WITH_RETRY_AFTER`` is that predicate, shared
with the raw reads of the provider health routes.

``FAILED_LLM_SPAN`` is that span predicate. The rollup job's backfill uses it too, to find the
oldest span that should have an error rollup: the two must agree, or a span that never makes a
row would open a gap that is backfilled again on every run.
"""

from sqlalchemy import text

# A predicate over `spans AS s`. `error_class IS NOT NULL` also matches the partial index
# `spans_project_error_class_started_idx`; failed spans stored before error classes existed
# have none and are not counted.
FAILED_LLM_SPAN = (
    "s.kind = 'llm' AND s.status = 'error' AND s.error_class IS NOT NULL "
    "AND NOT (s.attributes ? 'spanlight.fault.scenario')"
)

# A predicate over `spans AS s`: the span carried a retry-after (a number, not a stored null).
WITH_RETRY_AFTER = (
    "(jsonb_typeof(s.attributes -> 'spanlight.gateway.retry_after_s') = 'number' "
    "OR jsonb_typeof(s.attributes -> 'provider.retry_after_s') = 'number')"
)

DELETE_ERROR_ROLLUPS = text(
    """
    DELETE FROM error_rollups_hourly
    WHERE project_id = :project_id AND bucket_start >= :start AND bucket_start < :end
    """
)

INSERT_ERROR_ROLLUPS = text(
    f"""
    INSERT INTO error_rollups_hourly (
        project_id, bucket_start, environment, provider, model, error_class, rate_limit_kind,
        errors, with_retry_after
    )
    SELECT s.project_id,
           date_trunc('hour', s.started_at, 'UTC')  AS bucket_start,
           t.environment,
           s.provider,
           s.model,
           s.error_class,
           s.rate_limit_kind,
           count(*)                                 AS errors,
           count(*) FILTER (WHERE {WITH_RETRY_AFTER}) AS with_retry_after
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :project_id
      AND s.started_at >= :start
      AND s.started_at < :end
      AND {FAILED_LLM_SPAN}
    GROUP BY s.project_id, 2, t.environment, s.provider, s.model, s.error_class,
             s.rate_limit_kind
    """
)
