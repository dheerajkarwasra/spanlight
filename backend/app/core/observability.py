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
RATE_LIMIT_UNAVAILABLE = Counter(
    "spanlight_rate_limit_unavailable_total",
    "Rate-limit checks skipped because the limiter could not run, by scope "
    "(ingest, demo, api, gateway).",
    ["scope"],
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
