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
# Per-process control-loop evidence; labels never identify workers, jobs or tenants.
WORKER_PHASE = Gauge(
    "spanlight_worker_short_loop_phase", "Active short-loop phase (one hot).", ["phase"]
)
WORKER_PHASE_AGE = Gauge(
    "spanlight_worker_phase_age_seconds", "Monotonic age of the current short-loop phase."
)
WORKER_CLAIM_POLL_AGE = Gauge(
    "spanlight_worker_claim_poll_age_seconds", "Age of the last successful committed claim poll."
)
WORKER_STALLED = Gauge(
    "spanlight_worker_short_loop_stalled",
    "Whether short-loop phase or successful polling is stale.",
)
WORKER_STALLS = Counter(
    "spanlight_worker_short_loop_stalls_total", "Short-loop stall transitions, by phase.", ["phase"]
)
WORKER_OPERATION_FAILURES = Counter(
    "spanlight_worker_operation_failures_total", "Failed short database operations.", ["operation"]
)
# Queue state sampled by each worker on its heartbeat tick (`app/jobs/queue_gauges.py`). `kind`
# is the worker's registry frozen at start-up plus `other`; never a job, project or tenant. The
# series are removed when a sample fails, so an absent series means unknown, not empty. Each
# worker samples the whole queue: take `max` across workers, never `sum`.
JOBS_QUEUED_DUE = Gauge(
    "spanlight_jobs_queued_due",
    "Queued jobs whose run_after has passed, by kind.",
    ["kind"],
)
JOBS_QUEUED_OLDEST_AGE = Gauge(
    "spanlight_jobs_queued_oldest_age_seconds",
    "Database-clock age of the oldest due queued job, by kind; 0 when none waits.",
    ["kind"],
)
JOBS_RECLAIMABLE = Gauge(
    "spanlight_jobs_reclaimable",
    "Running jobs whose lease expired with attempts left, by kind.",
    ["kind"],
)
JOBS_RECLAIMABLE_OLDEST_AGE = Gauge(
    "spanlight_jobs_reclaimable_oldest_age_seconds",
    "Database-clock time since the oldest expired reclaimable lease, by kind; 0 when none.",
    ["kind"],
)
JOBS_QUEUE_SAMPLES = Counter(
    "spanlight_jobs_queue_samples_total",
    "Queue gauge samples, by outcome (ok, failed).",
    ["outcome"],
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
    "(ingest, ingest_project, demo, api, gateway).",
    ["scope"],
)
ADMISSION_REJECTIONS = Counter(
    "spanlight_admission_rejections_total",
    "Requests to /api or /v1 refused with 503 because API_MAX_IN_FLIGHT were already in flight.",
)
RATE_LIMIT_REJECTIONS = Counter(
    "spanlight_rate_limit_rejections_total",
    "Requests refused with 429 by a rate limit, by scope (ingest, ingest_project, "
    "ingest_project_concurrency, demo, api, "
    "gateway).",
    ["scope"],
)
INGEST_QUOTA_REJECTIONS = Counter(
    "spanlight_ingest_quota_rejections_total",
    "Ingest requests refused with 429 INGEST_QUOTA_EXCEEDED, by quota (spans_per_day, "
    "bytes_per_day).",
    ["quota"],
)
INGEST_QUOTA_REJECTED_SPANS = Counter(
    "spanlight_ingest_quota_rejected_spans_total",
    "Spans in the ingest requests a daily quota refused, by quota; the same count as the "
    "project's quota_spans / quota_bytes loss rows.",
    ["quota"],
)
INGEST_QUOTA_REPLAYS_ADMITTED = Counter(
    "spanlight_ingest_quota_replays_admitted_total",
    "Ingest requests at a used-up daily quota admitted because every span was already stored.",
)
INGEST_QUOTA_UTILISATION = Histogram(
    "spanlight_ingest_quota_utilisation",
    "used / limit of the daily quota closest to its cap, observed on every ingest request of a "
    "project with a daily cap, admitted or refused.",
    buckets=(0.1, 0.25, 0.5, 0.75, 0.9, 1.0, 1.5, 2.0),
)
# Per-process HTTP ingress only; no tenant, key, path or replica labels.
GATEWAY_IN_FLIGHT = Gauge(
    "spanlight_gateway_in_flight",
    "Admitted HTTP gateway requests, held through stream finalization.",
)
GATEWAY_ADMISSION_REJECTIONS = Counter(
    "spanlight_gateway_admission_rejections_total",
    "HTTP gateway requests refused before body/authentication because ingress is full, by the "
    "limit that refused them (count, bytes, key).",
    ["reason"],
)
for _reason in ("count", "bytes", "key"):
    GATEWAY_ADMISSION_REJECTIONS.labels(reason=_reason)  # each series exists from zero
del _reason
GATEWAY_ADMISSION_DURATION = Histogram(
    "spanlight_gateway_admission_duration_seconds",
    "Admitted HTTP gateway call duration through finalization, including failed calls.",
    buckets=(0.01, 0.1, 1, 5, 15, 30, 60, 300),
)
GATEWAY_REQUESTS = Counter(
    "spanlight_gateway_requests_total",
    "Gateway calls, by surface and outcome "
    "(ok, upstream_error, gateway_error, cache_hit, fault, budget_blocked).",
    ["surface", "outcome"],
)
GATEWAY_OVERHEAD = Histogram(
    "spanlight_gateway_overhead_seconds",
    "Time a gateway call spent in the gateway itself, without upstream time and waits, by surface "
    "and budget (with: the call reserved against a blocking budget or was refused by one; "
    "without: otherwise).",
    ["surface", "budget"],
    buckets=(0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.05, 0.1, 0.25),
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
    "Gateway calls refused by a blocking budget, by reason: reserved (the call's estimate did "
    "not fit next to the spend committed this period) or firing (the evaluated state).",
    ["reason"],
)
BUDGET_RESERVE_DURATION = Histogram(
    "spanlight_budget_reserve_seconds",
    "Time the gateway took to reserve a call's estimate against its blocking budgets "
    "(the price lookup, the counter row locks and the writes), admitted or refused.",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
)
BUDGET_RESERVE_PATH = Counter(
    "spanlight_budget_reserve_path_total",
    "Budget reserve calls handled by the fast function or the slow history repair path.",
    ["path"],
)
BUDGET_RESERVATIONS_REAPED = Counter(
    "spanlight_budget_reservations_reaped_total",
    "Budget reservations whose lease expired before their call settled, counted at the "
    "estimate by the reaper; a high rate means span writes or settles are failing.",
)
BUDGET_RESERVATIONS_HELD = Gauge(
    "spanlight_budget_reservations_held",
    "Budget reservations in flight across every project, set after each reaper pass.",
)
BUDGET_BUSY = Counter(
    "spanlight_budget_busy_total",
    "Budget edits and deletes whose try timed out waiting for a lock, by outcome: retried, or "
    "refused (the last try, answered 503 BUDGET_BUSY).",
    ["outcome"],
)
for _outcome in ("retried", "refused"):
    BUDGET_BUSY.labels(outcome=_outcome)  # each series exists from zero
del _outcome
BUDGET_DELTAS_COMPACTED = Counter(
    "spanlight_budget_deltas_compacted_total",
    "Budget spend deltas the reaper's job folded into their hour rows (admission folds its "
    "own budgets' deltas; this counts the budgets nobody admits against).",
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
    "Open insights across every project, by severity, refreshed after each project's detector run.",
    ["severity"],
)
DETECTOR_PROJECTS_STALE = Counter(
    "spanlight_detector_projects_stale_total",
    "Project detector jobs skipped because their pass was older than one period: the worker's "
    "short lane is behind the active projects.",
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
BILLING_BYPASS_CHECKS = Counter(
    "spanlight_billing_bypass_checks_total",
    "Bypass checks after a stored billing fetch, by outcome (ok, skipped, timeout, failed).",
    ["outcome"],
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

# No tenant IDs or bucket values: this records incomplete aggregate reads with bounded cardinality.
AGGREGATE_UNAVAILABLE_READS = Counter(
    "spanlight_aggregate_unavailable_reads_total",
    "Aggregate SQL reads whose source history cannot be proven complete.",
)

AGGREGATE_PENDING_DEPTH = Gauge(
    "spanlight_aggregate_pending_markers",
    "Pending correction markers, including disabled obligations.",
)
AGGREGATE_PENDING_AGE = Gauge(
    "spanlight_aggregate_oldest_pending_seconds",
    "Age of the oldest correction marker, including disabled obligations.",
)
AGGREGATE_RUNNABLE_DEPTH = Gauge(
    "spanlight_aggregate_runnable_markers",
    "Markers eligible with current product flags, regardless of the consumption switch.",
)
AGGREGATE_REPAIR_OUTCOMES = Counter(
    "spanlight_aggregate_repair_outcomes_total",
    "Committed marker classifications or failed attempts; "
    "a marker can have multiple classifications.",
    ["outcome"],
)

# Fixed labels: the 14 cleanup classes and a handful of outcomes; scope ids go to the worker log.
RETENTION_BATCHES = Counter(
    "spanlight_retention_batches_total",
    "Retention opportunities served, by cleanup class and outcome (deleted, empty, no_scope, "
    "lock_timeout, statement_timeout, timeout, error).",
    ["cleanup_class", "outcome"],
)
