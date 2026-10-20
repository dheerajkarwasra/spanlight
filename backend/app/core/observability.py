"""Prometheus metrics shared across the API."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "spanlight_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "spanlight_http_request_duration_seconds",
    "HTTP request latency, by route template.",
    ["method", "route"],
)
INGESTED_SPANS = Counter(
    "spanlight_ingested_spans_total",
    "Spans accepted by the ingestion pipeline.",
    ["source"],
)
REJECTED_SPANS = Counter(
    "spanlight_rejected_spans_total",
    "Spans rejected by the ingestion pipeline.",
    ["source"],
)
SPAN_NORMALIZATION_FAILURES = Counter(
    "spanlight_span_normalization_failures_total",
    "Spans rejected because preparing them raised an unexpected error (a bug), by error type.",
    ["error_type"],
)
JOBS_FINISHED = Counter(
    "spanlight_jobs_finished_total",
    "Background jobs finished, by kind and outcome.",
    ["kind", "outcome"],
)
OUTBOX_PENDING = Gauge(
    "spanlight_outbox_pending",
    "Notifications waiting in the outbox, set at the end of each delivery run.",
)
NOTIFICATIONS_DELIVERED = Counter(
    "spanlight_notifications_delivered_total",
    "Notification delivery attempts, by kind and outcome (sent, retry, failed, lost).",
    ["kind", "outcome"],
)
IDEMPOTENCY_REQUESTS = Counter(
    "spanlight_idempotency_requests_total",
    "Requests carrying an Idempotency-Key, by outcome "
    "(reserved, replayed, mismatch, in_progress, taken_over).",
    ["outcome"],
)
ROLLUP_DURATION = Histogram(
    "spanlight_rollup_duration_seconds",
    "Time one rollup_hourly run took, including any first-run backfill.",
)
METRICS_RAW_TAIL = Histogram(
    "spanlight_metrics_raw_tail_seconds",
    "How far back from now a metrics read takes raw spans instead of rollups; above about an hour"
    " the rollup job is behind.",
    buckets=(600, 1800, 3600, 5400, 7200, 10800, 21600, 43200, 86400),
)
RATE_LIMIT_UNAVAILABLE = Counter(
    "spanlight_rate_limit_unavailable_total",
    "Rate-limit checks skipped because the limiter could not run, by scope "
    "(ingest, demo, api, gateway).",
    ["scope"],
)
ADMISSION_REJECTIONS = Counter(
    "spanlight_admission_rejections_total",
    "Requests to /api or /v1 refused with 503 because API_MAX_IN_FLIGHT were already in flight.",
)
RATE_LIMIT_REJECTIONS = Counter(
    "spanlight_rate_limit_rejections_total",
    "Requests refused with 429 by a rate limit, by scope (ingest, demo, api, gateway).",
    ["scope"],
)
GATEWAY_REQUESTS = Counter(
    "spanlight_gateway_requests_total",
    "Gateway calls, by surface and outcome "
    "(ok, upstream_error, gateway_error, cache_hit, fault, budget_blocked).",
    ["surface", "outcome"],
)
GATEWAY_OVERHEAD = Histogram(
    "spanlight_gateway_overhead_seconds",
    "Time a gateway call spent in the gateway itself, without upstream time and waits, by surface.",
    ["surface"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
)
GATEWAY_UPSTREAM_DURATION = Histogram(
    "spanlight_gateway_upstream_duration_seconds",
    "Duration of one upstream attempt (to the full body, or to the first byte of a stream), "
    "by provider.",
    ["provider"],
)
GATEWAY_ATTEMPTS = Counter(
    "spanlight_gateway_attempts_total",
    "Upstream attempts, by provider and status (an HTTP status, timeout, connection_error, "
    "blocked or cancelled).",
    ["provider", "status"],
)
GATEWAY_RECORD_FAILURES = Counter(
    "spanlight_gateway_record_failures_total",
    "Gateway spans not written, by reason (error, rejected, overflow); the call was answered.",
    ["reason"],
)
ALERT_RULES_EVALUATED = Counter(
    "spanlight_alert_rules_evaluated_total",
    "Alert rules evaluated, by outcome (ok, no_data, error).",
    ["outcome"],
)
ALERT_TRANSITIONS = Counter(
    "spanlight_alert_transitions_total",
    "Alert rule state changes, by rule kind (threshold, anomaly, budget) and new state "
    "(firing, ok).",
    ["kind", "to_state"],
)
ALERT_EVALUATION_DURATION = Histogram(
    "spanlight_alert_evaluation_duration_seconds",
    "Time one evaluate_alerts pass took over every project.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0),
)
BUDGET_BLOCKS = Counter(
    "spanlight_budget_blocks_total",
    "Gateway calls refused because a blocking budget was exhausted.",
)
DETECTOR_RUNS = Counter(
    "spanlight_detector_runs_total",
    "Detector runs over one project, by detector kind and outcome (ok, error).",
    ["detector", "outcome"],
)
DETECTOR_DURATION = Histogram(
    "spanlight_detector_duration_seconds",
    "Time one detector took over one project's context, by detector kind.",
    ["detector"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
INSIGHTS_OPEN = Gauge(
    "spanlight_insights_open",
    "Open insights across every project, by severity, set at the end of each run_detectors pass.",
    ["severity"],
)
DETECTOR_PROJECTS_SKIPPED = Counter(
    "spanlight_detector_projects_skipped_total",
    "Projects a run_detectors pass did not start because its run budget ran out.",
)
SCORES_WRITTEN = Counter(
    "spanlight_scores_written_total",
    "Score rows inserted by the score write path, by source (api, human, eval). A retried score "
    "that was already stored is not counted.",
    ["source"],
)
PROMPT_READS = Counter(
    "spanlight_prompt_reads_total",
    "Prompt reads by API key, by outcome (hit, not_modified, not_found).",
    ["outcome"],
)
EVALUATOR_RUNS = Counter(
    "spanlight_evaluator_runs_total",
    "Evaluator runs, by evaluator kind (heuristic, llm_judge, webhook) and outcome: done, failed, "
    "budget_exceeded, evaluator_disabled, skipped (already started or ended), abandoned (failed "
    "by the reaper, charged its reservation), and from the "
    "sampler skipped_cap (over the per-cycle cap) and skipped_no_payload (no stored payload). "
    "The sampler's windows overlap, so a trace it skips may be counted in several cycles.",
    ["kind", "outcome"],
)
EVALUATOR_COST = Counter(
    "spanlight_evaluator_cost_usd_total",
    "US dollars evaluator runs were charged when they settled or were reaped, by evaluator kind "
    "(only judges "
    "spend). Each evaluator's monthly budget is in the dashboard, not in a metric.",
    ["kind"],
)
EXPERIMENT_ITEMS = Counter(
    "spanlight_experiment_items_total",
    "Experiment items stored, by outcome: done (the target answered) or error (a missing "
    "variable, a provider failure or a broken call).",
    ["outcome"],
)
FEATURE_USED = Counter(
    "spanlight_feature_used_total",
    "Uses of a feature, by feature name, counted once by the route or job that served it. The "
    "names are fixed by the program design (§3.5); `feature` is the only label and never gets a "
    "second one. The count stays on this instance.",
    ["feature"],
)
BILLING_FETCH_RUNS = Counter(
    "spanlight_billing_fetch_runs_total",
    "Fetches of a provider's cost report, by provider and outcome (ok, auth_failed, "
    "rate_limited, provider_error, invalid, skipped).",
    ["provider", "outcome"],
)
BILLING_FETCH_DURATION = Histogram(
    "spanlight_billing_fetch_duration_seconds",
    "Time one fetch of a provider's cost report took, every page included, by provider.",
    ["provider"],
    buckets=(0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)
BILLING_LINES_UPSERTED = Counter(
    "spanlight_billing_lines_upserted_total",
    "Provider cost lines written or updated by a fetch, by provider.",
    ["provider"],
)
COST_EXPLANATION_MISMATCH = Counter(
    "spanlight_cost_explanation_mismatch_total",
    "Cost explanation views that could not reproduce the stored cost from the stored usage and "
    "the price in force: a pricing bug signal. Counts each view, so one span read twice counts "
    "twice.",
)
PROVIDER_STATUS_POLLS = Counter(
    "spanlight_provider_status_polls_total",
    "Requests to a provider's status feed, by provider and outcome (fetched, not_modified, "
    "http_error, parse_error, too_large, timeout, blocked). A failed primary feed followed by "
    "its fallback counts twice. parse_error that keeps firing means the feed changed format.",
    ["provider", "outcome"],
)


def feature_used(feature: str) -> None:
    """Count one use of `feature`, a value from the program design's fixed list."""
    FEATURE_USED.labels(feature=feature).inc()


# The `feature` values the review loop (Bet 5) counts, from the program design (§3.5).
REVIEW_FEATURES = (
    "review_selection",
    "review_item_completed",
    "failure_category",
    "replay",
    "dataset_item_from_review",
    "experiment_interval",
)


OTLP_CONVENTION_SPANS = Counter(
    "spanlight_otlp_convention_spans_total",
    "OTLP spans accepted, by the exporter convention they were mapped from (gen_ai, "
    "openinference, vercel_ai, traceloop, none). A rejected span is not counted.",
    ["convention"],
)
METADATA_DROPPED = Counter(
    "spanlight_trace_metadata_dropped_total",
    "Trace metadata entries dropped by a lenient path, by path (otlp, gateway) and reason (key, "
    "value, type, count, header). Counts only: a dropped key or value is never logged.",
    ["path", "reason"],
)
ATTRIBUTION_REFRESH_DURATION = Histogram(
    "spanlight_attribution_refresh_duration_seconds",
    "Time one project's attribution refresh took (every kind and promoted key of the pass's "
    "days). A project past the worker's per-project time bound is logged and skipped, not "
    "observed.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
ATTRIBUTION_FOLDED_VALUES = Counter(
    "spanlight_attribution_folded_values_total",
    "Distinct values folded into an `other` row because a (day, environment, kind, key) had "
    "more than the daily cap, counted on every refresh that folds them (a refresh of the same "
    "day counts again). Counts only: no key or value is ever logged.",
)
REVIEW_SELECTED = Counter(
    "spanlight_review_selected_total",
    "Traces the review policy put into a review queue, by reason (doctor_finding, error, "
    "cost_outlier, random). Manual additions are not counted.",
    ["reason"],
)
REPLAY_RUNS = Counter(
    "spanlight_replay_runs_total",
    "Replays of a stored LLM request, by outcome: done, failed, budget_exceeded, "
    "gateway_budget_blocked, abandoned (failed by the reaper, charged its reservation).",
    ["outcome"],
)
REPLAY_COST = Counter(
    "spanlight_replay_cost_usd_total",
    "US dollars replays were charged when they settled or were reaped. Each project's monthly "
    "replay budget is in the dashboard, not in a metric.",
)
