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
| Requests are slow, connections run out, you want more replicas | [scale.md](scale.md) |
| Clients get 429, a burst of traffic, or charts are missing late spans | [ingestion-spike.md](ingestion-spike.md) |
| What "healthy" means, and the alerts that tell you it is not | [slo.md](slo.md) |
| Alerts do not arrive, deliveries are stuck or failed, evaluation is late, a webhook secret must change | [alerts.md](alerts.md) |
| The Doctor finds nothing or errors, a project's detector runs time out, an insight needs muting, the explanation budget is spent | [detectors.md](detectors.md) |
| Running the LLM gateway: embedded or standalone, key rotation, cache purge, the overhead panel | [gateway.md](gateway.md) |
| Seeding the demo's prompt, dataset, judge and experiments | [demo-evals.md](demo-evals.md) |

## Services at a glance

One image, three roles, plus Postgres. Postgres is the only datastore and also holds the job queue.

| Compose service | Runs | Listens | Health |
|---|---|---|---|
| `postgres` | PostgreSQL 17 | 5432 (internal) | `pg_isready` |
| `migrate` | `spanlight migrate && spanlight ensure-app-role --role spanlight_app`, then exits | none | exits 0 |
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

Healthy: the newest rows are `done` and under about 10 minutes old. Several `queued` rows that keep getting older mean no worker is running. A `failed` row has its reason in `last_error`.

## Related

- [Railway deployment guide](../deploy/railway.md): first-time setup, variables and monitoring on Railway.
- [Decision record: application master key](../decisions/0009-application-master-key-encryption.md)
- [Decision record: hourly rollups](../decisions/0006-hourly-rollups-with-bucketed-histograms.md)
