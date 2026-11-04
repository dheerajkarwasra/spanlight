# Operator runbooks

Step-by-step procedures for running a self-hosted Spanlight: Docker Compose (`deploy/compose.yaml`) or Railway (`deploy/railway/railway.ts`). Each runbook says when to use it, what you need, the exact commands with the output to expect, how to check that it worked, and how to back out.

## Pick a runbook

| Situation | Runbook |
|---|---|
| Shipping a new version | [deploy.md](deploy.md) |
| A new version is broken and you need the old one back | [rollback.md](rollback.md) |
| The database is lost or damaged | [restore.md](restore.md) |
| A secret leaked, or it is time to change one | [rotate-secrets.md](rotate-secrets.md) |
| An API key or personal access token leaked, or a user lost their authenticator | [revoke-key.md](revoke-key.md) |
| Personal data may have been exposed: who to notify, by when, and what to record | [breach-notification.md](breach-notification.md) |
| Requests are slow, connections run out, you want more replicas | [scale.md](scale.md) |
| Clients get 429, a burst of traffic, or charts are missing late spans | [ingestion-spike.md](ingestion-spike.md) |
| What "healthy" means, and the alerts that tell you it is not | [slo.md](slo.md) |
| Alerts do not arrive, deliveries are stuck or failed, evaluation is late, a webhook secret must change | [alerts.md](alerts.md) |
| The Doctor finds nothing or errors, a project's detector runs time out, an insight needs muting, the explanation budget is spent | [detectors.md](detectors.md) |
| Running the LLM gateway: embedded or standalone, key rotation, cache purge, the overhead panel | [gateway.md](gateway.md) |
| Seeding the demo's prompt, dataset, judge and experiments | [demo-evals.md](demo-evals.md) |
| Expired data is not going, retention batches time out, or the hourly run reports contended or missing cursors | [retention.md](retention.md) |

## Services at a glance

One image, three roles, plus Postgres. Postgres is the only datastore and also holds the job queue.

| Compose service | Runs | Listens | Health |
|---|---|---|---|
| `postgres` | PostgreSQL 17 | 5432 (internal) | `pg_isready` |
| `migrate` | `spanlight migrate && spanlight ensure-app-role --role spanlight_app && spanlight activate-aggregate-coverage --writers-drained --fresh-install --skip-if-projects-exist`, then exits | none | exits 0 |
| `api` | `uvicorn app.main:app` | 8000 (internal) | `/health/live`, `/health/ready`, `/metrics` |
| `worker` | `python -m app.jobs.worker` (retention, cleanup, rollups, notifications, backups, demo traffic) | 9100 (internal, only when `WORKER_METRICS_PORT` is set; Compose sets it) | `worker_heartbeat_age_s` in `/health/ready`; see [the worker check](#is-the-worker-alive) |
| `web` | Caddy: the dashboard, and a proxy for `/api`, `/v1`, `/health` and `/gw` (the LLM gateway) | `${WEB_PORT:-8080}` | `GET /` |
| `gateway` (optional) | `python -m app.gateway`, only with the `standalone-gateway` profile; see [gateway.md](gateway.md) | 8000 (internal) | `/health/live` |

The api and worker connect as the `spanlight_app` role, which is neither a superuser nor able to bypass row-level security. Only `migrate` uses the database owner. Never point `DATABASE_URL` of the api or worker at the owner: a superuser silently bypasses row-level security and every tenant can then read every other tenant's data.

`/metrics` is served by the api (`api:8000/metrics`) and by the worker (`worker:9100/metrics`, when `WORKER_METRICS_PORT` is set) and is not proxied by `web`. Scrape both on the internal network with `Authorization: Bearer <METRICS_TOKEN>`; without a `METRICS_TOKEN` the endpoints answer 404, with a wrong one 401. The api exposes request and ingest metrics, the worker exposes job, outbox, notification and rollup metrics; [slo.md](slo.md) uses both. `deploy/prometheus.yml` is an example scrape configuration for the Compose stack.

## Conventions used in these pages

Commands run from the repository root. For Docker Compose, each page starts by defining one shell function so you do not retype the long prefix:

```bash
spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
```

If you start the stack with an extra file (for example `deploy/compose.minio.yaml`), add another `-f` to the function; every compose command in these pages must see the same files and the same project.

Railway commands assume the project is linked (`railway status` names it) and that you pass `--service api`, `--service worker` or `--service web`.

`$BASE` stands for the public address of the dashboard, for example `http://localhost:8080` or `https://spanlight.example.com`.

Never paste a secret into a command line that is saved in shell history. These pages read secrets from the environment, from `deploy/.env` or from a pipe.

## First five minutes of any incident

1. Is the stack up and ready?

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health/ready"
   curl -sS "$BASE/health/ready"
   ```

   Healthy: `200` and `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0,"gateway_mode":"embedded"}`. `worker_heartbeat_age_s` is how many seconds ago the newest worker reported (it beats every 10 seconds; `null` means none ever has) and `outbox_backlog` is the number of pending notifications (capped at 10000). A `503` with `"database":"unavailable"` means the api cannot reach Postgres. A `503` with a `worker_heartbeat_age_s` over 120 or `null` means no worker is running (the check is on unless `WORKER_REQUIRED=false`); a `503` with the database and migrations `ok` and an `outbox_backlog` over 1000 can also mean the worker is not delivering notifications. After you start or restart the worker, allow up to a couple of minutes before you treat a `503` for that reason as real. `"migrations":"pending"` means the database schema is not the one this build expects, in either direction: a new build on an old schema, or an old build on a newer schema (see [rollback.md](rollback.md)). A connection error or a `502` from `web` means the api itself is down.

2. What is each container doing?

   ```bash
   spl ps
   spl logs --tail 100 api
   spl logs --tail 100 worker
   ```

   On Railway: `railway logs --service api --latest` and `railway logs --service worker --latest`.

3. Check recent changes: the last deploy ([deploy.md](deploy.md)), the last secret change ([rotate-secrets.md](rotate-secrets.md)) and whether traffic changed ([ingestion-spike.md](ingestion-spike.md)).

### Is the worker alive?

First look at `/health/ready`: `worker_heartbeat_age_s` should be under about 15 and never over 120. With Prometheus, `spanlight_jobs_finished_total` (labels `kind` and `outcome`) should keep rising on `worker:9100`; see [slo.md](slo.md). The same facts are in Postgres: the scheduler enqueues `rollup_hourly` every 5 minutes, `deliver_notifications` every 30 seconds and `cleanup_sessions` every hour, so a healthy worker leaves recent finished rows.

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT kind, status, outcome, attempts, now() - created_at AS age
     FROM jobs WHERE kind = 'rollup_hourly' ORDER BY id DESC LIMIT 3"
```

Healthy: the newest rows are `done` and under about 10 minutes old. Several `queued` rows that keep getting older mean no capable worker is progressing on those kinds. A `failed` row has its reason in `last_error`.

Workers leave unsupported job kinds untouched, including expired leases and exhausted jobs.
Compare waiting kinds with each worker's `worker_started` task list, including enabled enterprise
handlers. A growing unknown-kind backlog needs a capable worker or a corrected producer release;
it does not mean the jobs should be marked failed. An empty handler registry processes nothing.
Inspect the existing queue without adding tenant/job labels to metrics:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT kind, status, count(*) AS jobs, min(created_at) AS oldest
     FROM jobs WHERE status IN ('queued', 'running') GROUP BY kind, status ORDER BY oldest"
```

The same facts are on the worker's `/metrics`, sampled every heartbeat tick (10 s) in database
time, with the worker's registered kinds plus `other` as the only label values:

- `spanlight_jobs_queued_due{kind}` and `spanlight_jobs_queued_oldest_age_seconds{kind}`: queued
  jobs whose `run_after` has passed, and the age of the oldest. Future jobs are not counted.
- `spanlight_jobs_reclaimable{kind}` and `spanlight_jobs_reclaimable_oldest_age_seconds{kind}`:
  running jobs whose lease expired with attempts left (another worker will pick them up), and
  the time since the oldest lease expired. An expired lease on a final attempt is not counted;
  the next claim marks that job `failed`.
- `spanlight_jobs_queue_samples_total{outcome}`: `ok` or `failed` samples. A sample has its own
  1 s statement limit and 2 s budget; when it fails, the gauge series are removed rather than
  left stale or set to zero, so an absent series means "unknown", not "empty".

`other` is any queued kind this worker has no handler for; a growing `other` is the unknown-kind
backlog above. Every worker samples the whole queue, so with several workers take `max`, never
`sum`. A rising `spanlight_jobs_queued_oldest_age_seconds{kind="backup_database"}` or
`{kind="erase_end_user"}` above about 300 s while `spanlight_jobs_finished_total` keeps rising for
short kinds means the maintenance slot is held by the previous backup or erasure (see
[scale.md](scale.md#3-scale-the-worker)).

Use the [worker compatibility upgrade steps](deploy.md#worker-kind-and-payload-compatibility)
before producing a new kind or changing a payload contract. Mixed worker registries only isolate
unknown kinds; they do not establish general rolling-upgrade safety.

Heartbeat proves event-loop/database liveness, not short-loop progress. The worker's existing
metrics endpoint also exports these per-process signals (no worker, project or job IDs):

- `spanlight_worker_short_loop_phase{phase}`: one active fixed phase, `schedule`, `claim`,
  `short_execution`, `complete`, `fail`, `retry` or `idle`.
- `spanlight_worker_phase_age_seconds`: monotonic time in that phase.
- `spanlight_worker_claim_poll_age_seconds`: time since a successful committed claim poll;
  empty polls count, failed claims and long-job activity do not.
- `spanlight_worker_short_loop_stalled`: current watchdog assessment, sampled before each
  independent heartbeat attempt, even if that attempt fails.
- `spanlight_worker_short_loop_stalls_total{phase}`: transitions into a sampled stall.
- `spanlight_worker_operation_failures_total{operation}`: failed schedule, claim, complete,
  fail or retry database operations. Scheduling failures remain visible when claims succeed.

Internal operation envelopes are 1 second for scheduling, 0.5 seconds for claiming, and
1 second separately for each complete/fail/retry write, including commit and session exit.
Only those transactions use local 100 ms lock and 250 ms statement limits; handler sessions
retain their normal limits. These values were selected from quiet local PostgreSQL 17 lock
and cancellation samples, not production latency percentiles. Cancellation needs cleanup:
measured COMMIT timeout cleanup completed about 12 ms beyond its 0.5-second envelope.
A one-second cleanup margin is reserved for stall assessment; it is not a hard driver/network
cleanup guarantee. Failed/cancelled operation sessions are invalidated before exit, discarding
uncertain connections rather than waiting for a network rollback acknowledgment. With the
installed driver this closes the socket locally; a real withheld-rollback test returned in
about 104 ms, then replaced the connection and processed queued work. SQLAlchemy owns and
awaits its session-exit cleanup task even through cancellation; without invalidation this can
defer cancellation while rollback waits. Long handlers retain their existing timeout and
lease-extension behavior.

The watchdog permits the existing 50-second short handler plus a one-second cleanup margin.
Other phase allowances are 2 seconds for schedule/terminal persistence, 1.5 seconds for claim,
and 3 seconds for idle. A successful-poll age over 65.5 seconds detects repeated failed cycles
that keep changing phase. This includes operation budgets, normal two-second backoff, cleanup
margin and ten-second watchdog cadence; heartbeat attempts themselves can take five seconds.
Warnings use `worker_short_loop_stalled`; individual failures use `worker_operation_failed`.
An operating-system/event-loop freeze cannot be detected by another task on that same loop.
These in-process signals do not change `/health/ready` or persist per-worker readiness evidence.

A timeout means persistence is uncertain. A claim whose commit did not return successfully
must never execute; rollback or lease expiry recovers it. If delivery succeeds before terminal
persistence fails, inspect the job and let normal lease recovery/fencing decide the next attempt.
Do not mark it done manually or assume an external effect was undone: notification delivery
remains at least once and a retry can repeat the effect. Schedule failures retry independently
of already queued work; failed/no-work cycles retain their two-second backoff.

### Gateway HTTP capacity

Each gateway process admits a call only while three limits hold, in embedded and standalone mode
alike: `GATEWAY_MAX_IN_FLIGHT` calls (default 512), `GATEWAY_MAX_IN_FLIGHT_BYTES` of declared
request bodies (default 80 MiB; a chunked or undeclared body counts as the full 10 MiB request cap,
`GET`, `HEAD` and `OPTIONS` as nothing) and `GATEWAY_MAX_IN_FLIGHT_PER_KEY` calls with the same
gateway key, however it is sent (default 256). The byte budget bounds memory; the count is a
backstop and the per-key share keeps one caller from taking every slot. A slot starts before
authentication, rate limiting or body buffering and lasts through the complete ASGI call, including
stream cleanup. Excess requests receive provider-shaped `503 SERVICE_UNAVAILABLE` with
`Retry-After: 1` and a request ID without reading the body or reserving a budget. Existing provider
cleanup and settlement still own admitted calls.

The gateway cap is independent of `API_MAX_IN_FLIGHT`; health and metrics remain outside both.
Internal playground and worker calls do not enter this HTTP ingress cap. Limits and metrics are per
process: processes and replicas multiply capacity and memory consumption. Scrape each process and
budget memory for the other services and recorder backlog before adding replicas or raising this
setting. A per-key RPM limit does not replace concurrent ingress admission.

A bounded local calibration used Docker Desktop with 5 vCPU and 6.35 GiB, a separate application
process limited to 2 vCPU/2 GiB, a separate local HTTP provider/driver and disposable PostgreSQL 17
with the application role. At 8, 16 and 32 concurrent 10,484,736-byte streaming requests,
application RSS peaks across baseline and admission-enabled runs were approximately 425, 613 and
1,014 MiB from a warmed 175 MiB. Each plateau held exactly N upstream requests for 10 seconds;
health and authenticated dashboard reads remained responsive. A later calibration put 32 concurrent
256-byte streams at a 195 MB peak (184 MB base, about 0.35 MB a slot) against about 32 MB a slot for
~10 MiB bodies, which is why memory is budgeted by bytes and not by count. These short local
measurements are not production latency guarantees or a general memory budget; JSON parsing,
retries, recorder backlog and other internal calls also consume memory. Allocator retention can keep
RSS elevated after all streams close. The shared provider HTTP pool has not changed.

Monitor `spanlight_gateway_in_flight`, `spanlight_gateway_admission_rejections_total` and
`spanlight_gateway_admission_duration_seconds` (through finalization). Rejections carry a `reason`
label (`count`, `bytes`, `key`); none of these metrics has request, key or tenant labels. Persistent
rejections call for client backoff and measured capacity planning. Lowering the setting applies
after restart; admission does not cancel existing streams.

The 512-call default can need more database connections than the api pool holds under real
provider latency, and requests then fail with `503` after waiting `API_POOL_TIMEOUT_SECONDS`; the
load test behind the default used a fake provider that answered in 1 second. With the worker
stopped, pending `budget_bucket_deltas` rows of budgets nobody admits against keep growing until
the worker folds them (`spanlight_budget_deltas_compacted_total`, and a `budget_deltas_backlog`
warning at 100,000 rows for one budget).

## Related

- [Railway deployment guide](../deploy/railway.md): first-time setup, variables and monitoring on Railway.
- [Decision record: application master key](../decisions/0009-application-master-key-encryption.md)
- [Decision record: hourly rollups](../decisions/0006-hourly-rollups-with-bucketed-histograms.md)
