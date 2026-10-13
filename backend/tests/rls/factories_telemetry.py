"""Row factories for the telemetry, rollup, export and insight tables."""

from tests.rls.row_spec import HEX16, HEX32, RowFactory, parent

TELEMETRY = (
    RowFactory(
        table="traces",
        sql=(
            "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
            f"VALUES (:project_id, {HEX32}, now(), now()) RETURNING *"
        ),
        touch="tags",
    ),
    RowFactory(
        table="spans",
        sql=(
            "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
            "started_at, ended_at) "
            f"VALUES (:project_id, :trace_id, {HEX16}, 'llm', 'call', 'ok', now(), now()) "
            "RETURNING *"
        ),
        touch="name",
        parents=(parent("traces", trace_id="trace_id"),),
    ),
    RowFactory(
        table="span_rollups_hourly",
        sql=(
            "INSERT INTO span_rollups_hourly (project_id, bucket_start, kind, span_count, errors, "
            "input_tokens, output_tokens, cached_tokens, unpriced_calls, latency_buckets, "
            "ttft_buckets) "
            "VALUES (:project_id, date_trunc('hour', now()), 'llm', 1, 0, 0, 0, 0, 0, "
            "array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32])) RETURNING *"
        ),
        touch="span_count",
    ),
    RowFactory(
        table="trace_rollups_hourly",
        sql=(
            "INSERT INTO trace_rollups_hourly (project_id, bucket_start, traces, errored_traces) "
            "VALUES (:project_id, date_trunc('hour', now()), 1, 0) RETURNING *"
        ),
        touch="traces",
    ),
    RowFactory(
        table="user_stats_daily",
        sql=(
            "INSERT INTO user_stats_daily (project_id, day, external_user_id, traces, llm_calls, "
            "errors, unpriced_calls, tokens) "
            f"VALUES (:project_id, current_date, {HEX32}, 1, 1, 0, 0, 0) RETURNING *"
        ),
        touch="traces",
    ),
    RowFactory(
        table="exports",
        sql=(
            "INSERT INTO exports (id, project_id, format, filters) "
            "VALUES (gen_random_uuid(), :project_id, 'jsonl', '{}') RETURNING *"
        ),
        touch="filters",
    ),
    RowFactory(
        table="insights",
        sql=(
            "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, title, "
            "summary, failure_layer, certainty, evidence, suggested_fix, verification, "
            "first_seen_at, last_seen_at, occurrences) "
            f"VALUES (gen_random_uuid(), :project_id, 'test', 'info', 'open', {HEX32}, 'title', "
            "'summary', 'client', 'measured', '{}', 'fix', 'verify', now(), now(), 1) RETURNING *"
        ),
        touch="title",
    ),
    RowFactory(
        table="insight_explanations",
        sql=(
            "INSERT INTO insight_explanations (id, project_id, insight_id, model, cost_usd) "
            "VALUES (gen_random_uuid(), :project_id, :insight_id, 'model', 0) RETURNING *"
        ),
        touch="model",
        parents=(parent("insights", insight_id="id"),),
    ),
    RowFactory(
        table="detector_runs",
        sql=(
            "INSERT INTO detector_runs (id, project_id, detector, window_start, window_end, "
            "duration_ms, findings) "
            "VALUES (gen_random_uuid(), :project_id, 'detector', now(), now(), 0, 0) RETURNING *"
        ),
        touch="duration_ms",
    ),
)
