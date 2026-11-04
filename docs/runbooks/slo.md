# Service level objectives and alerts

## Purpose and when to use

This page defines what "healthy" means for a Spanlight deployment, how to measure it, and the Prometheus rules that tell you when it is not. Use it to set up monitoring once, and during an incident to decide how bad things are and which runbook to open.

## The objectives

| # | Objective | Target | Window | Measured at |
|---|---|---|---|---|
| 1 | Availability | 99.5 % of requests are not server errors | 30 days, rolling | the api, `/api/*` and `/v1/*` |
| 2 | Ingest latency | p95 of `POST /v1/traces` and `POST /v1/otlp/traces` is at most 200 ms | 30 days, rolling | the api |
| 3 | Dashboard latency | p95 of the dashboard read routes is at most 300 ms | 30 days, rolling | the api |

Details:

- **Availability.** A request is good unless the api answers it with a 5xx status (an unhandled exception is counted as a 500). 4xx responses are not failures: they are the client's doing, and that includes `429` (see [ingestion-spike.md](ingestion-spike.md) for sustained 429s). `/health/*` and `/metrics` are not counted. The error budget is 0.5 %, which is 3 hours 36 minutes of total outage in 30 days.
- **Ingest latency.** The time the api takes to handle an ingest request, from the first byte in to the last byte out, as seen by the api. It includes validation, redaction, pricing and the database transaction. It excludes the network between the sender and Caddy.
- **Dashboard latency.** The same measure for `GET` on a project's traces (list and detail), sessions, filters and the three metrics routes (`overview`, `timeseries`, `models`).

These targets assume one api replica with a sensibly sized Postgres. If you run a much larger workload, change the numbers to match what you promise your users; keep the alert expressions in step.

## Metrics that exist

The api serves `/metrics` on port 8000, and the worker serves its own on port 9100 when `WORKER_METRICS_PORT` is set (`deploy/compose.yaml` sets it; the port is not published). Both use the `METRICS_TOKEN` bearer token. These are the series the rules below use; all are defined in `backend/app/core/observability.py`. Each process reports only what it records.

| Metric | Labels | Meaning |
|---|---|---|
| `spanlight_http_requests_total` | `method`, `route`, `status` | Requests handled. `route` is the matched path template, such as `/v1/traces` or `/api/v1/projects/{project_id}/traces`. |
| `spanlight_http_request_duration_seconds` | `method`, `route` | Request latency histogram (`_bucket`, `_sum`, `_count`) with the client library's default buckets: 5, 10, 25, 50, 75, 100, 250, 500, 750 ms, 1, 2.5, 5, 7.5, 10 s. |
| `spanlight_ingested_spans_total` | `source` (`native`, `otlp`) | Spans accepted. |
| `spanlight_rejected_spans_total` | `source` | Spans rejected by validation. |
| `spanlight_span_normalization_failures_total` | `error_type` | Spans rejected because preparing them raised an unexpected error. Each one is a bug: anything above zero is worth a look (see [ingestion-spike.md](ingestion-spike.md#1-see-what-is-happening)). |
| `spanlight_rate_limit_rejections_total` | `scope` (`ingest`, `demo`, `api`) | Requests refused with 429 by a rate limit. |
| `spanlight_idempotency_requests_total` | `outcome` | Requests carrying an `Idempotency-Key`, by outcome. |
| `spanlight_jobs_finished_total` | `kind`, `outcome` | **Worker.** Background jobs finished. `outcome` is `done`, `skipped` (finished without work because an integration is not configured or a budget said no), `failed` (out of attempts), `retry` (failed, will run again) or `lease_lost`. |
| `spanlight_rollup_duration_seconds` | none | **Worker.** Time one `rollup_hourly` run took (same default buckets; runs over 10 seconds land in `+Inf`). |
| `spanlight_jobs_queued_due`, `spanlight_jobs_queued_oldest_age_seconds` | `kind` | **Worker.** Queued jobs past their `run_after`, and the database-clock age of the oldest, sampled every 10 s; `kind` is the worker's registry plus `other`. Series are removed when a sample fails: alert on `absent()` as well as on age. Across workers take `max`, not `sum`. |
| `spanlight_jobs_reclaimable`, `spanlight_jobs_reclaimable_oldest_age_seconds` | `kind` | **Worker.** Running jobs whose lease expired with attempts left, and the time since the oldest expired lease. |
| `spanlight_jobs_queue_samples_total` | `outcome` | **Worker.** Queue gauge samples, `ok` or `failed`. |
| `spanlight_outbox_pending` | none | **Worker.** Notifications waiting to be sent, set at the end of each delivery run. |
| `spanlight_notifications_delivered_total` | `kind`, `outcome` | **Worker.** Delivery attempts by outcome (`sent`, `retry`, `failed`). |

If the worker is not scraped (no `WORKER_METRICS_PORT`, or a Railway deployment without a scraper), use the SQL and log checks in [Worker and data checks](#worker-and-data-checks) instead; they work either way and are the secondary check when metrics are on.

## Prerequisites: collect the metrics

1. **Scrape the api.** `/metrics` is not proxied by `web`; scrape the api on the internal network, with the bearer token from `METRICS_TOKEN` (without one, the endpoint answers `404`):

   ```yaml
   # prometheus.yml
   scrape_configs:
     - job_name: spanlight-api
       metrics_path: /metrics
       scrape_interval: 15s
       authorization:
         type: Bearer
         credentials_file: /etc/prometheus/spanlight-metrics-token
       static_configs:
         - targets: ["api:8000"]
   rule_files:
     - /etc/prometheus/spanlight-rules.yml
   ```

   Add the worker as a second target with the same token, in a job named `spanlight-worker`. `deploy/prometheus.yml` is a complete example for the Compose stack:

   ```yaml
     - job_name: spanlight-worker
       metrics_path: /metrics
       authorization:
         type: Bearer
         credentials_file: /etc/prometheus/spanlight-metrics-token
       static_configs:
         - targets: ["worker:9100"]
   ```

   The worker answers `404` until `METRICS_TOKEN` is set on the worker as well as the api, and `401` for a wrong token.
   With more than one api or worker replica, a static target reaches a different replica on each scrape under one `instance` label and the counters jump. Use `dns_sd_configs` instead, as `deploy/prometheus.yml` does (`names: ["api"]`, `type: A`, `port: 8000`), so each replica is a target of its own.
   On Compose, Prometheus has to join the stack's network (`spanlight_default`, from `name: spanlight` in `deploy/compose.yaml`) to resolve `api`. On Railway, run Prometheus as a service of the same project and use the api's private domain and port 8000 as the target.

2. **Keep 30 days of data.** The 30-day rules need Prometheus to retain at least that much (`--storage.tsdb.retention.time=30d`; its default is 15 days).

3. **Probe readiness from outside.** The api cannot report that it is down, and neither can a failing scrape tell you whether the problem is the token or the service. `/health/ready` also answers 503 when no worker has reported for 120 seconds or when more than 1000 notifications are overdue by more than 10 minutes, so one probe covers the database, migrations, the worker and the outbox. Probe it through the public address with the Prometheus blackbox exporter (or any uptime monitor that alerts on a non-200):

   ```yaml
     - job_name: spanlight-ready
       metrics_path: /probe
       params:
         module: [http_2xx]
       static_configs:
         - targets: ["https://spanlight.example.com/health/ready"]
       relabel_configs:
         - source_labels: [__address__]
           target_label: __param_target
         - source_labels: [__param_target]
           target_label: instance
         - target_label: __address__
           replacement: blackbox-exporter:9115
   ```

4. **Check that the route labels match the expressions below.** Print the distinct route templates the api reports:

   ```bash
   spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
   spl exec -T api python -c "import os, urllib.request as u; r = u.Request('http://127.0.0.1:8000/metrics', headers={'Authorization': 'Bearer ' + os.environ['METRICS_TOKEN']}); print(u.urlopen(r).read().decode())" \
     | grep '^spanlight_http_requests_total' | sed -E 's/.*route="([^"]*)".*/\1/' | sort -u
   ```

   Expected (among others): `/v1/traces`, `/v1/otlp/traces`, `/api/v1/projects/{project_id}/traces`, `/api/v1/projects/{project_id}/metrics/overview`. Generate some traffic first, since a route appears only after its first request. If your templates differ, fix the `route=~` matchers below.

## Rules

The rules are in `deploy/spanlight-rules.yml`, the one source for them. `deploy/prometheus.yml` loads it (`rule_files`) and its header shows the mount. Before use, edit the items its header lists: the expected api and worker replica counts (`spanlight:api_expected_replicas`, `spanlight:worker_expected_replicas`, 1 each as shipped), the readiness probe rule, and the worker group if the worker is not scraped. Check it with `promtool check rules deploy/spanlight-rules.yml`, then reload Prometheus.

What each rule does:

| Rule | Fires when | Severity |
|---|---|---|
| `spanlight:http_error_ratio:rate5m` ... `rate3d` | Recording: share of `/api` and `/v1` requests that were 5xx over 5 m, 30 m, 1 h, 6 h and 3 d (empty 5xx counts as 0) | |
| `spanlight:http_availability:ratio30d`, `spanlight:http_error_budget_remaining:ratio30d` | Recording: the 30-day SLI (0.995 or more passes) and the unspent share of its error budget | |
| `SpanlightAvailabilityFastBurn` | Error ratio over 14.4 × 0.005 on both the 1 h and 5 m windows for 2 m (2 % of the 30-day budget in an hour) | page |
| `SpanlightAvailabilitySlowBurn` | Over 6 × 0.005 on both 6 h and 30 m for 15 m (5 % of the budget in 6 h) | page |
| `SpanlightAvailabilityBudgetDrain` | Over 0.005 on both 3 d and 6 h for 1 h: on course to miss 99.5 % | ticket |
| `SpanlightNotReady` | The blackbox probe of `/health/ready` fails for 3 m | page |
| `SpanlightMetricsScrapeDown` | An api target fails its scrape for 5 m (down, or `METRICS_TOKEN` mismatch) | ticket |
| `SpanlightApiReplicasMissing` | Fewer api replicas answer the scrape than `spanlight:api_expected_replicas`, or none, for 5 m. Targets come from DNS, so a replica that stops leaves DNS and its `up` goes stale rather than 0, which the scrape-down rule cannot see | ticket |
| `SpanlightIngestLatencyP95High` | Ingest p95 over 200 ms for 10 m | page |
| `SpanlightDashboardLatencyP95High` | Dashboard read p95 over 300 ms for 10 m | page |
| `SpanlightIngestRateLimited` | Ingest rate-limit rejections over 1/s for 15 m | ticket |
| `SpanlightSpansRejected` | Over 5 % of incoming spans rejected for 15 m | ticket |
| `SpanlightWorkerScrapeDown` | A worker target fails its scrape for 5 m (down, no `WORKER_METRICS_PORT`, or token mismatch) | ticket |
| `SpanlightWorkerReplicasMissing` | Fewer worker replicas answer than `spanlight:worker_expected_replicas`, or none, for 5 m | ticket |
| `SpanlightRollupsStalled` | Under 3 finished hourly rollups in 30 m (about 6 expected) for 10 m | page |
| `SpanlightRollupSlow` | Rollup p95 over 5 s for 30 m (a task is stopped at 50 s) | ticket |
| `SpanlightJobFailed` | A job ran out of attempts in the last hour | ticket |
| `SpanlightBackupMissing` | No backup finished in 26 h, for 1 h; skipped counts as missing | ticket |
| `SpanlightOutboxBacklog` | Over 1000 notifications pending for 15 m | ticket |
| `SpanlightNotificationsFailing` | A notification kind failed permanently in the last hour | ticket |

The worker rules are in their own group, `spanlight-worker-alerts`: delete it if the worker is not scraped (for example Railway without a scraper), since `or vector(0)` makes the rollup and backup rules fire when there is no data.

The `runbook` annotations name files in this directory; point them at wherever you host these pages. The worker counters start when the worker process starts, so after a worker restart the first `increase` window has less history; the `for:` delays absorb that. `SpanlightBackupMissing` needs a worker that has run for part of the day, and it fires if backups are not configured, which is the point.

### Availability alerts

When `SpanlightAvailabilityFastBurn` or `SpanlightAvailabilitySlowBurn` fires:

1. Run the first-five-minutes check in [README.md](README.md#first-five-minutes-of-any-incident).
2. Find which route fails: `sum by (route, status) (rate(spanlight_http_requests_total{status=~"5.."}[5m]))`.
3. If it started with a deploy, [roll back](rollback.md). If it is `/health/ready` returning 503 with `"database":"unavailable"`, the database is down or unreachable. If you lost the database, [restore](restore.md).
4. If only ingest fails, see [ingestion-spike.md](ingestion-spike.md); if everything is slow, [scale.md](scale.md).

### Notes on measurement

- **Histogram precision.** The latency histogram has fixed buckets, and `histogram_quantile` interpolates linearly inside a bucket. The 200 ms target sits inside the 100 to 250 ms bucket and the 300 ms target inside the 250 to 500 ms bucket, so the reported p95 is an estimate that can be off by up to the width of its bucket. If you need an exact threshold, count requests under a bucket edge instead, for example the share of ingest requests that finish in 250 ms or less:

  ```promql
  sum(rate(spanlight_http_request_duration_seconds_bucket{method="POST",route=~"/v1/traces|/v1/otlp/traces",le="0.25"}[10m]))
  / sum(rate(spanlight_http_request_duration_seconds_count{method="POST",route=~"/v1/traces|/v1/otlp/traces"}[10m]))
  ```

- **All statuses are in the latency histogram.** The histogram has no `status` label, so fast `401` and `429` responses are included and pull the p95 down slightly. A sustained pile of 429s therefore makes ingest latency look better than it is for accepted requests.
- **No traffic, no ratio.** With zero requests, the error ratio is empty and the burn alerts do not fire. `SpanlightNotReady`, `SpanlightMetricsScrapeDown` and `SpanlightApiReplicasMissing` cover the case where the service is down and silent.
- **Counter resets.** Restarts reset the api's counters; `rate` and `increase` handle it.
- **A Postgres restart costs a few 503s.** The api does not test pooled connections before use, so after Postgres restarts the first request on each stale connection answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 5` and the api logs `db_connection_lost`: at most the pool size (10) per api process, usually fewer, since the first failure makes the pool replace its other connections. It happens once per restart and clears within seconds; a steady stream of `db_connection_lost` means connections keep dying (a proxy or firewall closing idle ones), not a restart.

## Worker and data checks

These are the secondary checks. With worker metrics on, the `spanlight-worker-alerts` rules above cover them; the SQL below works without Prometheus, and it is the way to look at a single failed job. Run it as the database owner, from a scheduled job or a monitor that can run a query. The scheduler enqueues `rollup_hourly` every 5 minutes, `deliver_notifications` every 30 seconds and the nightly `backup_database` at 03:00 UTC. `/health/ready` adds two direct checks: `worker_heartbeat_age_s` under 120 and `outbox_backlog` not stuck.

| Check | Query | Healthy | Act when |
|---|---|---|---|
| Worker is scheduling | `SELECT now() - max(created_at) FROM jobs WHERE kind = 'rollup_hourly'` | under 10 minutes | over 15 minutes: the worker is down or cannot reach Postgres ([README.md](README.md#is-the-worker-alive)) |
| Jobs are finishing | `SELECT count(*) FROM jobs WHERE kind = 'rollup_hourly' AND status = 'done' AND created_at > now() - interval '30 minutes'` | 5 or 6 | 0 while the first check is healthy: jobs are stuck or failing |
| Jobs are not failing | `SELECT kind, count(*) FROM jobs WHERE status = 'failed' AND created_at > now() - interval '1 day' GROUP BY kind` | no rows | any row; read `last_error` of those jobs |
| Last backup | `SELECT max(created_at) FROM jobs WHERE kind = 'backup_database' AND status = 'done' AND outcome = 'ok'` | within the last 26 hours | older: the backup is failing or skipped ([restore.md](restore.md) has the setup) |
| Backups configured | `SELECT outcome, count(*) FROM jobs WHERE kind = 'backup_database' AND status = 'done' GROUP BY outcome` | `ok` | `skipped_not_configured`: no backups are being taken |

Run a check from the shell like this:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT now() - max(created_at) AS since_last_rollup_job FROM jobs WHERE kind = 'rollup_hourly'"
```

Finished jobs are kept for 7 days. In the worker logs, a failed job writes an error-level `job_failed` line, and a finished one `job_done` with its `outcome`:

```bash
spl logs --since 1h worker | grep job_failed
```

If `SENTRY_DSN` is set, a failed job also arrives in Sentry as an error event.

## Error budget policy

Check `spanlight:http_error_budget_remaining:ratio30d` weekly. While more than half the budget is left, ship as usual. When less than a quarter is left, ship only fixes and security updates, and take a backup before every deploy ([deploy.md](deploy.md)). When it reaches zero, stop feature deploys until the 30-day availability is back above 99.5 %.

## Verification

After setting up:

- `promtool check rules /etc/prometheus/spanlight-rules.yml` reports the rules as valid.
- In Prometheus, `up{job="spanlight-api"}` and `up{job="spanlight-worker"}` are `1` and `spanlight_http_requests_total` and `spanlight_jobs_finished_total` have series with the routes from prerequisite 4.
- `spanlight:http_availability:ratio30d` returns a number once the recording rule has run (it needs traffic).
- To test an alert path end to end, stop the api for a few minutes in a non-production stack (`spl stop api`) and confirm `SpanlightNotReady` fires, then `spl start api`.
