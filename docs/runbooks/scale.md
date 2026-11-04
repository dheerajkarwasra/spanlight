# Scale Spanlight

## Purpose and when to use

Add capacity: requests are slow, the api runs out of database connections, background jobs fall behind, or Postgres is the limit. Work from the cheapest change to the most expensive: find the bottleneck first, give the single bottleneck more resources, and add replicas only where the table below says they help.

## What scales how

One image, three roles.

| Role | Stateless? | How to add capacity | Notes |
|---|---|---|---|
| `api` | Yes. Sessions, idempotency keys, rate-limit buckets and login throttles all live in Postgres. | More replicas behind a load balancer, about one per vCPU you give the api; one replica uses one core at most | Each replica uses up to 30 database connections (see below) |
| `worker` | Yes. The queue lives in Postgres (`FOR UPDATE SKIP LOCKED`, leases and fencing tokens). | Add replicas | Each worker runs one short job at a time, plus at most one experiment or export and at most one backup or end-user erasure beside it |
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

Every connection comes from a pool set in code. The maximum each process can open:

| Process | Pools | Max connections | Where |
|---|---|---|---|
| `api` (`python -m app.server`) | Requests: 10, may overflow by another 10; waits up to `API_POOL_TIMEOUT_SECONDS` (1 s). Idempotency keys: `IDEMPOTENCY_POOL_SIZE`, default 5, no overflow; waits up to `IDEMPOTENCY_POOL_TIMEOUT_SECONDS` (5 s). Rate limiting: `RATE_LIMIT_POOL_SIZE`, default 5, no overflow; waits up to `RATE_LIMIT_POOL_TIMEOUT_SECONDS` (0.25 s) | **30** | `create_app` in `backend/app/main.py`; the request pool size is `REQUEST_POOL_SIZE` in `backend/app/config.py`, not an environment setting |
| Standalone `gateway` (`python -m app.gateway`, optional) | Requests 10 + 10 and rate limiting 5, as on the api; no idempotency pool | **25** | `create_gateway_app` in `backend/app/main.py` |
| `worker` | One pool of 2 that may overflow to 4, plus one `pg_dump` connection (as `BACKUP_DATABASE_URL`) while the nightly backup runs | **5** | `_resources` in `backend/app/jobs/worker_lifecycle.py`; `backend/app/backups/service.py` |
| `migrate` on Compose (the `migrate` service) | `spanlight migrate` (Alembic, no pool), then `ensure-app-role`, then `activate-aggregate-coverage`, one connection each, one after the other | **1** | `deploy/compose.yaml`; `backend/alembic/env.py`, `backend/app/db/roles.py`, `backend/app/rollups/activation.py` |
| `migrate` on Railway (the api's pre-deploy command) | `spanlight migrate`, then `ensure-app-role`, one after the other | **1** | `preDeploy` in `deploy/railway/railway.ts`; `backend/alembic/env.py`, `backend/app/db/roles.py` |
| Admin commands (`spanlight create-user`, `backup`, `alerts`, ...) and your own `psql` | One or two per command | headroom | `backend/app/cli.py` |

The api keeps three pools apart so that a burst on one cannot starve the others: an idempotency reservation takes a second connection while the request still holds its first, and rate-limit checks run outside the request's transaction. The api's pools are the same whether `/gw/*` is served by the api or by a standalone gateway.

The app connects as a role without superuser rights, so it can use at most `max_connections` minus `superuser_reserved_connections` (3 by default). Budget the backup and migration connections inside that too, whatever role they use.

### Compose and self-host

```
30 x api + 25 x gateway + 5 x worker + 1 (migrate) + 10 (admin, psql)  <=  max_connections - 3
```

`docker compose up -d` stops each old container before it starts its replacement, so a deploy does not hold old and new replicas at once; the `migrate` service runs while the old api and worker are still up, which the `1` covers. The Compose file ships `max_connections = 200` (with the other settings in [Tuning Postgres](../../docs-site/src/content/docs/self-hosting.mdx#tuning-postgres), sized for a 2 vCPU / 4 GB host): that allows six api replicas and one worker (30 × 6 + 5 + 1 + 10 = 196 ≤ 197), or five api replicas and up to seven workers, or five api replicas, a standalone gateway and one worker (150 + 25 + 5 + 11 = 191). With Postgres's own default of 100, which a managed Postgres may keep, it allows two api replicas and up to five workers; for a third api replica, raise `max_connections` first.

### Railway

`deploy/railway/railway.ts` declares `api`, `worker` and `web`, sets no replica count (so one each), and gives `api` and `worker` `drainingSeconds: 75`. The live project was set up in the Railway dashboard and differs from `railway.ts`, so read the real values there before you plan: each service's replica count (**Settings → Deploy**), whether a standalone gateway service exists, any pool variables (`IDEMPOTENCY_POOL_SIZE`, `RATE_LIMIT_POOL_SIZE`) and `SHOW max_connections` on the Railway Postgres. Do not assume them.

On a deploy, Railway starts the new deployment, waits for its healthcheck, and keeps the old one running while it drains, up to `drainingSeconds`. For that window a service holds old and new replicas together, so the budget doubles for every service being deployed. Assume all replicas of a service are replaced together unless you have checked otherwise:

```
2 x (30 x api + 25 x gateway + 5 x worker) + 1 (pre-deploy migrate) + 10  <=  max_connections - 3
```

At `max_connections = 100` that is one api replica and one worker (2 × 35 + 11 = 81 ≤ 97). Two api replicas and one worker need `max_connections` of at least 2 × 65 + 11 + 3 = 144, so raise it first. Smaller idempotency and rate-limit pools do not make them fit at 100: even at 1 each, an api replica can open 22 connections and the deploy overlap needs 2 × (44 + 5) + 11 = 109 > 97.

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
      - -c
      - max_parallel_workers_per_gather=2
      - -c
      - max_parallel_workers=8
```

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.connections.yaml --env-file deploy/.env up -d
spl exec postgres psql -U postgres -tA -c "SHOW max_connections"
```

Expected: `300`. From now on every compose command in these runbooks must carry the same `-f` pair, so update your `spl` function. More connections cost memory on the database host: size it first. On Railway, change the setting through Railway's Postgres service settings.

## 3. Scale the worker

Safe at any time. Workers claim jobs with `FOR UPDATE SKIP LOCKED` under a 60-second lease with a fencing token, and the scheduler enqueues each periodic job once per period with a dedupe key, so any number of workers can run together without doubling a job. The demo traffic job, which spends money, is scheduled once per 30 minutes and runs with a single attempt however many workers there are.

More workers also helps with long jobs. A worker has two long-job slots beside its short loop, so a long job does not stop rollups, notifications or cleanup: one for an experiment run or an export, and one reserved for maintenance, the nightly backup (up to three hours) or an end-user erasure. A due backup or erasure therefore starts within one poll (2 s) even while an experiment runs for hours; measured locally, handler entry came 2.0 s after the due time. While a slot is taken, the worker claims no second job of that slot's kinds: a second experiment waits for the first, and an erasure that becomes due during the backup waits for the backup to finish, which can exceed the 300 s target the maintenance slot is sized for (watch `spanlight_jobs_queued_oldest_age_seconds{kind="erase_end_user"}`). A second worker is the remedy for both: it brings a second pair of slots. During maintenance beside an experiment a worker holds one more database connection than before (its pool allows 4, and the backup's `pg_dump` takes a fifth).

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

Add replicas, not a bigger instance, once one api process is busy. The scale-out unit is the container: each api replica runs one uvicorn process, and its `/metrics` and `/health/ready` describe that process alone. A process handles requests on one event loop, so request handling uses one core (only password hashing runs on two extra threads); a replica gains little from more than about one vCPU, so give the api N vCPUs as N replicas. The image does not use `uvicorn --workers`: the api starts through `app.server`, which owns graceful shutdown (it finishes in-flight requests and gateway stream recordings before the pools close), and Uvicorn's multi-process supervisor would start plain servers without it.

**What one process holds.** Measured on a dedicated 16 vCPU Linux host with 10 million spans stored, k6 and the fake provider pinned to their own cores ([Final load campaign at 10 million spans](../performance.md#final-load-campaign-at-10-million-spans-2026-11-02)):

| | 1 api replica | 3 api replicas |
|---|---|---|
| Ingest, 100-span requests: highest rate held (p95 under 500 ms, under 1 % failures, no load the generator could not send) | 1,500 spans/s | 4,000 spans/s |
| Gateway calls, instant fake provider: highest rate held | 100 calls/s | 200 calls/s |

Past the limit, requests wait up to `API_POOL_TIMEOUT_SECONDS` (1 s) for a database connection and then get `503` with `Retry-After` (at 2,000 spans/s on one replica: 15.6 % `503`, p95 2.5 s). Ingest scales close to linearly with replicas; the gateway does not, because every gateway call also writes its project's row for the current hour in `aggregate_dirty_hours`, and more replicas mean more writers waiting on that one row (51 ms per write with one replica, 149 ms with three). Postgres had CPU to spare in every run. Earlier, on one Mac with the load generator on the same machine, one process held about 20 modelled companies (1.2 ingest requests a second of 20 spans plus 6 dashboard reads a second each) and a second replica added nothing, because everything shared the same cores: measure with the load generator on another host.

When you do add replicas:

1. Check the connection budget above.
2. Remember what is in front of them. The Caddyfile has one upstream setting, `API_UPSTREAM` (default `api:8000`). Docker's DNS returns every replica's address for the name `api`, but Caddy resolves one hostname once per connection and would keep sending a client's requests to the same replica. Plain `--scale api=2` therefore does not balance. `deploy/compose.replicas.yaml` is the supported way to run two replicas on Compose:

   ```bash
   docker compose -f deploy/compose.yaml -f deploy/compose.replicas.yaml --env-file deploy/.env up -d --build
   docker compose -f deploy/compose.yaml -f deploy/compose.replicas.yaml --env-file deploy/.env ps api
   ```

   Expected: two `api` containers, `spanlight-api-1` and `spanlight-api-2`. The file sets `api` to two replicas and gives `web` `API_UPSTREAM: spanlight-api-1:8000 spanlight-api-2:8000`, so Caddy balances between the named containers. Its limits, from the file itself:

   - **Two replicas as shipped.** The container names are written into `API_UPSTREAM` and `GATEWAY_UPSTREAM`. For N replicas, add `--scale api=N` to the `up` command and list all N names in both upstreams in a copy of the file; its header comment has a three-replica example. A replica that is not listed gets no traffic.
   - **A stopped replica is skipped.** When Caddy cannot open a connection to one upstream (the replica is restarting, stopped or gone), it retries the request on another for up to 5 s (`lb_try_duration` in `deploy/Caddyfile`), whatever the method, since nothing was sent. A request that already reached a replica is not retried, except a `GET`.
   - **The project must be named `spanlight`** (`name:` in `deploy/compose.yaml`); container names are `<project>-<service>-<index>`. Starting the stack with `-p other` breaks the names.
   - **It was written to check that replicas share their rate limits** (both draw from the same Postgres buckets), not as a high-availability setup: there is still one `web`, one Postgres and one host.
   - Add the same extra `-f` to your `spl` function, or later commands will not see the override and will recreate `web` and `api` with one replica.

   For replicas on several hosts, put your own load balancer in front of the api containers and point `API_UPSTREAM` at it, or use a platform with built-in service replicas, such as Railway.
3. Check the connection budget again with the replicas running (step 1 above).

Each replica caches the API key rows it has looked up for `API_KEY_CACHE_TTL_SECONDS` (10 s), so a revoked key keeps working on the other replicas for up to that long ([revoke-key.md](revoke-key.md)).

Behind a load balancer or proxy, set `TRUSTED_PROXIES` to its address ranges, so rate limits and session records see the real client IP and not the proxy's. The api reads `X-Forwarded-For` from Caddy, which takes it from `X-Real-IP` only for trusted proxies (see `deploy/Caddyfile`).

## 5. Scale Postgres

- **Resources first.** More CPU and memory, and fast disk. This is the lever for slow dashboard queries and slow ingest.
- **Settings follow the resources.** The Compose file sizes Postgres for a 2 vCPU / 4 GB node (`shared_buffers=1GB`, `effective_cache_size=3GB`, `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=200`, `wal_compression=on`, `random_page_cost=1.1`, `autovacuum_vacuum_cost_limit=1000`, `jit=off`, and Postgres' own parallel-worker values pinned: `max_parallel_workers_per_gather=2`, `max_parallel_workers=8`). On a bigger node raise `shared_buffers` to about a quarter of the memory and `effective_cache_size` to about three quarters, as in [Tuning Postgres](../../docs-site/src/content/docs/self-hosting.mdx#tuning-postgres). Postgres needs about 1.5 GB free for the shipped values; on a smaller host it does not start until you lower them.
- **Parallel workers.** The trace list's value sorts (`duration`, `cost`, `tokens`, `retry_cost_usd`) have no index and sort every trace of the window, which Postgres splits across parallel workers. Over 30 days at 10 000 traces a day a sorted page took about 40 ms with two workers and 100 to 140 ms without them ([Trace list sorts](../performance.md#sorts)). A query that finds the server's pool of parallel workers used up runs serial, so keep the pool large enough for the value sorts that run at once:
  - `max_parallel_workers_per_gather=2`: keep it at 2 on every size. A third or fourth worker shortens one sort less than it takes from the sorts beside it.
  - `max_parallel_workers`: the pool all queries share. On the 2 vCPU / 4 GB node keep the shipped 8 (four parallel sorts at once; past that the two cores are the limit, not the pool). On a bigger node set it to the number of vCPUs Postgres has, at least 8: 16 on the 16 vCPU host of the load campaign.
  - `max_worker_processes` (default 8) caps the pool, so raise it with `max_parallel_workers` beyond 8 (`max_worker_processes=16` beside `max_parallel_workers=16`). Unlike the other two, it takes effect only after a Postgres restart.

  Check how often parallel plans got fewer workers than planned (PostgreSQL 18 counts it in `pg_stat_database.parallel_workers_launched` against `parallel_workers_to_launch`; on 17, `EXPLAIN ANALYZE` of a slow value sort shows `Workers Planned` against `Workers Launched`).
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
