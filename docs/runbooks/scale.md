# Scale Spanlight

## Purpose and when to use

Add capacity: requests are slow, the api runs out of database connections, background jobs fall behind, or Postgres is the limit. Work from the cheapest change to the most expensive: find the bottleneck first, give the single bottleneck more resources, and add replicas only where the table below says they help.

## What scales how

One image, three roles.

| Role | Stateless? | How to add capacity | Notes |
|---|---|---|---|
| `api` | Yes. Sessions, idempotency keys, rate-limit buckets and login throttles all live in Postgres. | More CPU first, then more replicas behind a load balancer | Each replica uses up to 30 database connections (see below) |
| `worker` | Yes. The queue lives in Postgres (`FOR UPDATE SKIP LOCKED`, leases and fencing tokens). | Add replicas | Each worker runs one short job at a time, plus at most one long job (a backup or an export) beside it |
| `web` | Yes (static files and a proxy) | Rarely needed | Caddy handles far more than the api behind it |
| `postgres` | No. The only datastore. | A bigger instance, more disk, tuned settings | Everything else waits on it |

Rate limits are shared across replicas through Postgres (ingest 50 requests/s with a burst of 100 per API key, 20/s burst 40 per credential for bearer reads of `/api/v1`, 10 demo sessions per client address refilling at 10 an hour), so adding api replicas does not multiply the limits.

## Prerequisites

- Shell access and the Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- The latency and error-rate numbers from [slo.md](slo.md), or at least the `http_request` log lines (`duration_ms`, `status`).

## 1. Find the bottleneck

```bash
spl ps
docker stats --no-stream
```

`docker stats` shows CPU and memory per container. Then look at the database.

**Connections.** Count them by role and compare with the limit:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT usename, count(*) FROM pg_stat_activity WHERE datname = 'spanlight' GROUP BY usename ORDER BY 2 DESC"
spl exec postgres psql -U postgres -d spanlight -tA -c "SHOW max_connections"
```

Expected on a healthy single-replica stack: `spanlight_app` with a handful of connections, well under `max_connections` (200 in the shipped Compose file; Postgres's own default, which a managed Postgres may keep, is 100).

**Slow statements right now:**

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT pid, now() - query_start AS running, state, left(query, 80) AS query
     FROM pg_stat_activity WHERE datname = 'spanlight' AND state <> 'idle' ORDER BY 2 DESC LIMIT 10"
```

**Jobs falling behind.** A growing queue means the worker cannot keep up or is not running:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT kind, status, count(*), min(created_at) AS oldest FROM jobs
    WHERE status IN ('queued', 'running') GROUP BY kind, status ORDER BY kind"
```

Periodic jobs are scheduled with one job per period, so a handful of queued rows is normal. Tens of old `queued` rows of one kind are not.

With the worker's metrics scraped (`worker:9100`, see [slo.md](slo.md)), the same picture in Prometheus:

```promql
spanlight_outbox_pending
sum by (kind, outcome) (rate(spanlight_jobs_finished_total[15m]))
histogram_quantile(0.95, sum by (le) (rate(spanlight_rollup_duration_seconds_bucket[1h])))
```

A rising `spanlight_outbox_pending`, `failed` or `retry` outcomes, or a rollup p95 close to the 50-second task limit mean the worker is the bottleneck: add a worker below, or look at the database first if everything is slow.

## 2. The connection budget

Every api process holds three pools, set in `backend/app/main.py`:

| Pool | Size | Setting |
|---|---|---|
| Main (requests) | 10, may overflow by another 10; waits up to `API_POOL_TIMEOUT_SECONDS` (1 s) | size fixed in code, wait in the environment |
| Idempotency (reserving and completing an `Idempotency-Key`) | `IDEMPOTENCY_POOL_SIZE`, default 5, no overflow; waits up to `IDEMPOTENCY_POOL_TIMEOUT_SECONDS` (5 s) | environment |
| Rate limiting | `RATE_LIMIT_POOL_SIZE`, default 5, no overflow; waits up to `RATE_LIMIT_POOL_TIMEOUT_SECONDS` (0.25 s) | environment |

So one api replica can open up to **20 + 5 + 5 = 30** connections. A worker holds a pool of 2 that can overflow to 4. The `migrate` service, a backup (`pg_dump` opens one connection) and your own `psql` sessions come on top.

```
30 x (api replicas) + 4 x (worker replicas) + 10 (headroom)  <=  max_connections - 3
```

`3` is Postgres's default `superuser_reserved_connections`. The Compose file ships `max_connections = 200` (with the other settings in [Tuning Postgres](../../docs-site/src/content/docs/self-hosting.mdx#tuning-postgres), sized for a 2 vCPU / 4 GB host): that allows six api replicas and one worker (30 × 6 + 4 + 10 = 194 ≤ 197), or five api replicas and several workers. With Postgres's own default of 100, which a managed Postgres may keep, it allows two api replicas and one or two workers; for a third, raise `max_connections` first.

What running out looks like:

- The main pool is full: requests wait up to `API_POOL_TIMEOUT_SECONDS` (1 s) for a connection, then fail with `503 SERVICE_UNAVAILABLE` and `Retry-After: 5`, and the api logs `db_pool_timeout`. A statement that runs longer than `API_STATEMENT_TIMEOUT_SECONDS` (10 s) is cancelled the same way and logged as `db_statement_timeout`. Latency climbs first.
- The idempotency pool is full: a request with an `Idempotency-Key` fails after `IDEMPOTENCY_POOL_TIMEOUT_SECONDS`.
- The rate-limit pool is full: the check is **skipped** and the request goes through, so limits are not enforced while it lasts. The api counts each skipped check in `spanlight_rate_limit_unavailable_total{scope}` and logs `rate_limit_unavailable` with the scope (at most once a minute per scope). If that counter rises, the stack is over its connection budget.

Raise `max_connections` on the Compose Postgres with an override file, then restart Postgres (it is a restart, so plan a brief outage). The override replaces the service's whole `command`, so repeat every `-c` value the shipped `deploy/compose.yaml` sets and change only `max_connections`:

```yaml
# deploy/compose.connections.yaml
services:
  postgres:
    command:
      - postgres
      - -c
      - jit=off
      - -c
      - shared_buffers=1GB
      - -c
      - effective_cache_size=3GB
      - -c
      - work_mem=16MB
      - -c
      - maintenance_work_mem=256MB
      - -c
      - max_connections=300
      - -c
      - wal_compression=on
      - -c
      - random_page_cost=1.1
      - -c
      - autovacuum_vacuum_cost_limit=1000
```

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.connections.yaml --env-file deploy/.env up -d
spl exec postgres psql -U postgres -tA -c "SHOW max_connections"
```

Expected: `300`. From now on every compose command in these runbooks must carry the same `-f` pair, so update your `spl` function. More connections cost memory on the database host: size it first. On Railway, change the setting through Railway's Postgres service settings.

## 3. Scale the worker

Safe at any time. Workers claim jobs with `FOR UPDATE SKIP LOCKED` under a 60-second lease with a fencing token, and the scheduler enqueues each periodic job once per period with a dedupe key, so any number of workers can run together without doubling a job. The demo traffic job, which spends money, is scheduled once per 30 minutes and runs with a single attempt however many workers there are.

More workers also helps with long jobs. A worker runs one long job (the nightly backup, which can take up to three hours, or an export) at a time in a slot of its own and keeps claiming short jobs (rollups, notifications, cleanup) beside it, so a backup does not stop those. While its slot is taken, a worker claims no second long job, so a second worker lets an export run during the backup.

```bash
spl up -d --scale worker=2
spl ps worker
```

Expected: two `worker` containers running. Check that both started:

```bash
spl logs --since 2m worker | grep worker_started
```

Expected: two `worker_started` lines, and the `jobs` table shows one `rollup_hourly` row per five-minute period, not two.

On Railway, raise the worker service's replica count in its settings.

## 4. Scale the api

Prefer a bigger instance (CPU) over more replicas until you need availability or have saturated one process. The scale-out unit is the container: each api replica runs one uvicorn process (the image does not use `--workers`), and its `/metrics` and `/health/ready` describe that process alone.

**What one process holds.** One api process served about 20 modelled companies before requests started failing, where a company is 1.2 ingest requests a second of 20 spans each plus 6 dashboard reads a second with an API key (overview over 24 hours and 7 days, trace list over 1 hour). That was measured on one Mac (Apple M4, Docker with 10 CPUs and 8 GB) with k6 on the same host and 3.7 to 4.1 million spans in the database; the api process, at 100 % of one core, was the limit, not Postgres. Past it, the api answers `503` with `Retry-After` instead of queueing. **How that scales with replicas is not measured**: on that Mac a second replica did not add capacity, because k6, Postgres and the replicas shared one machine. Measure on your own hardware, with the load generator on another host, before you count on N replicas holding N times as much.

When you do add replicas:

1. Check the connection budget above.
2. Remember what is in front of them. The Caddyfile has one upstream setting, `API_UPSTREAM` (default `api:8000`). Docker's DNS returns every replica's address for the name `api`, but Caddy resolves one hostname once per connection and would keep sending a client's requests to the same replica. Plain `--scale api=2` therefore does not balance. `deploy/compose.replicas.yaml` is the supported way to run two replicas on Compose:

   ```bash
   docker compose -f deploy/compose.yaml -f deploy/compose.replicas.yaml --env-file deploy/.env up -d --build
   docker compose -f deploy/compose.yaml -f deploy/compose.replicas.yaml --env-file deploy/.env ps api
   ```

   Expected: two `api` containers, `spanlight-api-1` and `spanlight-api-2`. The file sets `api` to two replicas and gives `web` `API_UPSTREAM: spanlight-api-1:8000 spanlight-api-2:8000`, so Caddy balances between the named containers. Its limits, from the file itself:

   - **Two replicas as shipped.** The container names are written into `API_UPSTREAM` and `GATEWAY_UPSTREAM`. For N replicas, add `--scale api=N` to the `up` command and list all N names in both upstreams in a copy of the file; its header comment has a three-replica example. A replica that is not listed gets no traffic.
   - **The project must be named `spanlight`** (`name:` in `deploy/compose.yaml`); container names are `<project>-<service>-<index>`. Starting the stack with `-p other` breaks the names.
   - **It was written to check that replicas share their rate limits** (both draw from the same Postgres buckets), not as a high-availability setup: there is still one `web`, one Postgres and one host.
   - Add the same extra `-f` to your `spl` function, or later commands will not see the override and will recreate `web` and `api` with one replica.

   For replicas on several hosts, put your own load balancer in front of the api containers and point `API_UPSTREAM` at it, or use a platform with built-in service replicas, such as Railway.
3. Check the connection budget again with the replicas running (step 1 above).

Each replica caches the API key rows it has looked up for `API_KEY_CACHE_TTL_SECONDS` (10 s), so a revoked key keeps working on the other replicas for up to that long ([revoke-key.md](revoke-key.md)).

Behind a load balancer or proxy, set `TRUSTED_PROXIES` to its address ranges, so rate limits and session records see the real client IP and not the proxy's. The api reads `X-Forwarded-For` from Caddy, which takes it from `X-Real-IP` only for trusted proxies (see `deploy/Caddyfile`).

## 5. Scale Postgres

- **Resources first.** More CPU and memory, and fast disk. This is the lever for slow dashboard queries and slow ingest.
- **Settings follow the resources.** The Compose file sizes Postgres for a 2 vCPU / 4 GB node (`shared_buffers=1GB`, `effective_cache_size=3GB`, `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=200`, `wal_compression=on`, `random_page_cost=1.1`, `autovacuum_vacuum_cost_limit=1000`, `jit=off`). On a bigger node raise `shared_buffers` to about a quarter of the memory and `effective_cache_size` to about three quarters, as in [Tuning Postgres](../../docs-site/src/content/docs/self-hosting.mdx#tuning-postgres). Postgres needs about 1.5 GB free for the shipped values; on a smaller host it does not start until you lower them.
- **Retention.** Each project keeps spans for `retention_days` (30 by default; the longest allowed is 90). The worker's hourly retention job deletes older spans. A shorter retention is the cheapest way to shrink the largest tables, `spans` and `traces`.
- **Windows.** At any window length, dashboard queries read whole closed hours from the hourly rollups and only the partial first and last hours, plus the hours the rollup job has not finished yet, from raw spans. The raw part stays small while the rollup job keeps up. If the job falls behind, up to 24 hours are read from raw spans and dashboards slow down, so check the worker first.
- **Disk.** Watch the size of the volume (`pgdata` in Compose):

  ```bash
  spl exec postgres psql -U postgres -d spanlight -c \
    "SELECT relname, pg_size_pretty(pg_total_relation_size(oid)) FROM pg_class
      WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace ORDER BY pg_total_relation_size(oid) DESC LIMIT 5"
  ```

## Verification

After a change, over at least ten minutes:

- `curl -fsS "$BASE/health/ready"` returns `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`.
- The connection count from step 1 is under the budget, and there are no `rate_limit_unavailable` log lines: `spl logs --since 10m api | grep -c rate_limit_unavailable` prints `0`.
- The ingest and dashboard p95 from [slo.md](slo.md) are back under their targets.
- `jobs` has no growing backlog.

## Escape hatch

Scale back down with the same command and a smaller number (`spl up -d --scale worker=1`, `--scale api=1`). Containers are stateless, so nothing is lost. If raising `max_connections` made the database host run out of memory, remove the override file from your `-f` list and run `up -d` again; Postgres restarts with the shipped settings.
