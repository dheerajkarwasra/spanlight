# Performance

How fast Spanlight ingests and serves traces, how that is measured, and how to repeat the measurement. The numbers come from a load test that anyone can run against the Compose stack; this page records the method and, once a run has produced them, the results.

## Targets

| Path                 | Load                                                                                                                                                                                        | Target                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Ingestion            | `POST /v1/traces`: 500 spans a second, as 5 requests a second of 100 spans                                                                                                                  | p95 below 200 ms                                                |
| Dashboard overview   | `GET /api/v1/projects/{id}/metrics/overview`: 20 requests a second over four time windows, with 1 million spans stored (`realistic`; the `full` stress run stores 10 million)               | p95 below 300 ms                                                |
| Trace list           | `GET /api/v1/projects/{id}/traces`: 20 requests a second, with 1 million spans stored (`realistic`; `full`: 10 million)                                                                     | p95 below 300 ms                                                |
| LLM gateway overhead | `POST /gw/v1/chat/completions`: 50 requests a second for 60 s, against a fake provider that answers at once, compared with the same 50 requests a second sent to the fake provider directly | overhead (p95 through the gateway minus p95 direct) below 20 ms |
| Alert evaluation     | `spanlight alerts evaluate`: one pass over 1 000 seeded alert evaluations (50 projects, 18 rules and 2 budgets each, 7 days of spans and rollups)                                           | median of three passes below 10 s                               |

These are the same figures as the service level objectives in the [SLO runbook](runbooks/slo.md). Beside the latency each scenario has to meet three more conditions:

- fewer than 1 % of the requests fail, and a `429` counts as a failure;
- more than 99 % of the response checks pass (the status, and for each script that the body has what it should);
- the arrival rate is held, which is the threshold `dropped_iterations` equal to 0: k6 drops an iteration when it has no free virtual user left, and a server that cannot keep up would otherwise look fast.

A script that misses any of them exits non-zero.

## Method

- **Tool.** [k6](https://k6.io), from the official `grafana/k6` image at a pinned release. Every scenario uses the `constant-arrival-rate` executor, which sends requests at a fixed rate whatever the response time. A closed-loop test would slow down when the server slows down and hide the problem.
- **Request path.** Through the `web` service (Caddy) on port 8080, to the API and on to Postgres: the path of every self-hosted request, including authentication and the rate-limit check.
- **Stack.** `deploy/compose.yaml` as shipped: one `api`, one `worker`, `web` and Postgres 17 (its stock settings plus `jit=off`; see [Tuning Postgres](../docs-site/src/content/docs/self-hosting.mdx) for the ones a larger host should change). The only change is an override file ([`backend/load/compose.load.yaml`](../backend/load/compose.load.yaml)) that publishes Postgres on `127.0.0.1` for the seeder, turns the demo traffic job off and, for the gateway scenario, adds a fake provider, allows the gateway to call it over plain `http://` (`GATEWAY_ALLOW_INSECURE_BASE_URLS`) and gives the api and worker a throwaway `CREDENTIALS_KEYS`. It changes no limit, pool size or code path.
- **Credentials.** Ingestion uses one API key with `ingest:write`. A key is limited to 50 requests a second, and the test sends 5. The read routes accept API keys with `traces:read`, and a key is limited to 20 reads a second (bursts of 40), which is the same as the target rate. The read scripts therefore spread their requests over ten keys, one per virtual user, so the limit never caps the test.
- **Overview.** The window rotates through 1 hour, 24 hours, 7 days and 30 days. The first two read the raw spans and give exact percentiles. The other two read the hourly rollups and say `"approximate": true`. Two checks in the script confirm that each response reports the source its window length calls for (`approximate`), and that the window has data, which for the 7-day and 30-day windows means the rollups hold rows. The p95 of each window is shown in the results but is not a threshold; the threshold is the p95 over all requests.
- **Between scripts.** Before each k6 script the workflow waits for the database to settle ([`settle.sh`](../backend/load/settle.sh)): it polls `pg_stat_activity` until no query of the application role is executing and no job is running, for at most 2 minutes, and carries on either way. k6 gives up on a slow request after its timeout, but the server keeps running the query, and the worker may still be rolling up hours, so without the pause one scenario would start on the leftovers of the one before.
- **Trace list.** Three requests in ten are the first page the dashboard opens with (last 24 hours, no filter), three filter by one model (two) or to traces with errors (one), and four sort the first page by duration, cost, tokens or retry cost (one each). Each variant is a metric of its own (`traces_duration_<variant>`), and the summary has a table of each variant's median, p95 and maximum below the thresholds.

### Gateway overhead

The [LLM gateway](runbooks/gateway.md) sits between an application and its provider, so the number that matters is the time a call spends in Spanlight and not the provider's. The test removes the provider from the measurement:

- **Fake provider.** [`backend/load/fake_provider.py`](../backend/load/fake_provider.py) is a small ASGI app that answers `POST /v1/chat/completions` and `GET /v1/models` with fixed bodies. It runs as a service of the load stack and waits `FAKE_PROVIDER_DELAY_MS` before it answers, which is 0 unless you set it.
- **Setup.** [`backend/load/seed_gateway.py`](../backend/load/seed_gateway.py) creates an `openai_compatible` credential on the fake provider (its API key sealed with the stack's `CREDENTIALS_KEYS`, so the gateway opens it as it opens any credential), a route that sends every call there once, and a gateway key with no rate limits, no model list and no cache. A rate limit would throttle the run, and a cached answer would measure the cache. The organization ceiling `GATEWAY_ORG_RPM_CEILING` is left unset.
- **Two scenarios, one after the other,** each at 50 requests a second for 60 seconds: `gateway` sends a short chat completion through the web service (Caddy) to `/gw/v1/chat/completions`, the path a self-hosted call takes, including the key lookup, the limit checks, the route, the sealed credential, the upstream call and the span that is written afterwards; `direct` sends the same request to the fake provider on its published port. The script warms both paths up before the clock starts.
- **Thresholds.** The p95 of `gateway` below 25 ms and the p95 of `direct` below 5 ms, so a slow provider cannot hide the overhead, plus the same failure, check and arrival-rate conditions as the other scenarios.
- **The figure.** The overhead is the p95 of `gateway` minus the p95 of `direct`, and it has to be under 20 ms. k6 cannot compare two thresholds, so the script computes it, shows it in the results table and stores it in `gateway.json` as `overhead`; the workflow fails the job when it is not under the limit. It includes the hop through Caddy, which `direct` does not take.
- **Every profile.** The scenario runs after the other three in all three profiles, for 60 seconds whatever the profile, because it does not read the seeded spans.

## Data

[`backend/load/seed.py`](../backend/load/seed.py) fills the database before any request. It creates an organization, a project, one ingest key and ten read keys, and stores the spans through the same pipeline that handles `POST /v1/traces` (validation, normalization, pricing and the upsert), called in process because the ingest limit would turn 10 million spans into hours of waiting. Then it builds the rollups and runs `VACUUM ANALYZE`, so the first request sees what a settled system looks like.

| Property | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Traces   | 1 to 6 spans each (3.5 on average): a single LLM call, or a root span with children                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Kinds    | `llm` calls, `tool` and `retrieval` spans under a `chain` root                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Models   | five with a published price and one without, so some costs are unknown, as in production                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Failures | about 2 % of the spans have status `error`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Payloads | input and output of 200 to 800 bytes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Time     | start times spread over the 28 days before the seeder started, inside the default retention of 30 days so the retention job leaves them alone. The batches are stored oldest to newest, each holding the traces that start in its slice of the 28 days, and about 2 % of the traces land up to an hour late, like a retry or a batching client. Rows that started close together therefore sit close together in the table, as after real traffic; stored in random order, one day of spans would be scattered over a fifth of the table, which no real system produces. The overview and trace list scripts ask for windows that end where the data ends, not at the wall clock, so every window is full however long the seeding took |

## Node

All three profiles run on a GitHub-hosted standard `ubuntu-latest` runner: 4 vCPU and 16 GB of memory. The stack and the load generator share those four cores, so the load generator takes some of the CPU the stack could have used. The runner is shared hardware, so repeated runs differ a little; compare runs of the same profile on the same kind of runner.

## Run it

**On GitHub.** Open the Actions tab, choose **Load test** ([`load-smoke.yml`](../.github/workflows/load-smoke.yml)) and **Run workflow**. The profile `realistic` (the default) seeds 1 million spans, about 33 000 LLM calls a day for 30 days, which is a small production team, and runs every scenario for 1 minute. The profile `smoke` seeds 100 000 spans and runs every scenario for 30 seconds, to check the setup. The profile `full` is the stress run: it seeds 10 million spans and runs each scenario for 2 minutes (ingestion for 3), and takes several hours, almost all of it seeding; the seeder prints its rate and stops early if it projects the seeding to overrun its time budget. After the three scenarios on the seeded data it runs the gateway overhead scenario. The run's summary page shows the environment (date, runner, versions, number of spans seeded) and a table for each script, the raw k6 summaries are attached as an artifact, and the job is red if any threshold was missed.

**On your machine.** See [`backend/load/README.md`](../backend/load/README.md): start the stack with the override file, seed it, and run the scripts with k6.

## Results

`realistic` profile, run [37952458454](https://github.com/dheerajkarwasra/spanlight/actions/runs/37952458454) on 2026-10-09 at commit `2f533e4`: a GitHub-hosted `ubuntu-latest` runner (4 vCPU, 15.6 GiB, image ubuntu24 20261004.327.1), PostgreSQL 17.11, k6 2.3.0, with 1 000 000 spans in 286 612 traces stored. Every threshold passed.

| Scenario           | Rate                             | Median  | p95      | p99      | Target (p95) | Failed requests | Dropped iterations |
| ------------------ | -------------------------------- | ------- | -------- | -------- | ------------ | --------------- | ------------------ |
| Ingestion          | 5 requests/s of 100 spans, 1 min | 98.9 ms | 111.5 ms | 180.0 ms | 200 ms       | 0 %             | 0                  |
| Dashboard overview | 20 requests/s, 1 min             | 38.9 ms | 50.3 ms  | 83.6 ms  | 300 ms       | 0 %             | 0                  |
| Trace list         | 20 requests/s, 1 min             | 14.1 ms | 16.0 ms  | 18.8 ms  | 300 ms       | 0 %             | 0                  |

### Gateway overhead

Run [37997565780](https://github.com/dheerajkarwasra/spanlight/actions/runs/37997565780) on 2026-10-10 at commit `38568be`, same runner type and stack (1 000 000 spans stored), 50 requests a second for 60 s per scenario. The threshold passed.

| Scenario                                        | Median  | p95         | p99     | Failed requests    | Dropped iterations |
| ----------------------------------------------- | ------- | ----------- | ------- | ------------------ | ------------------ |
| Through the gateway (`/gw/v1/chat/completions`) | 11.1 ms | 17.1 ms     | 38.2 ms | 0 %                | 0                  |
| Direct to the fake provider                     | 0.4 ms  | 0.5 ms      | 0.7 ms  | 0 %                | 0                  |
| **Overhead (p95 difference)**                   |         | **16.6 ms** |         | target below 20 ms |                    |

The overhead includes the Caddy hop and the gateway's own request to the provider, which the direct scenario does not make, so it reads slightly high. The same run measured the dashboard again: ingestion p95 114.7 ms, overview p95 47.5 ms, trace list p95 15.1 ms.

The `full` stress run with 10 million spans has not been recorded yet.

## Alert evaluation

The [`evaluate_alerts` job](runbooks/alerts.md) runs once a minute and evaluates every enabled rule and budget of every project. The target is that a pass over 1 000 of them finishes in under 10 seconds, so that a worker has most of the minute to spare.

**Method.** [`backend/load/seed_alerts.py`](../backend/load/seed_alerts.py) creates 50 projects, each with 18 rules (threshold and anomaly, over every metric, with windows from 15 minutes to a day, some filtered by environment, provider or model, and several that share a metric, window and filters so the per-pass metric cache matters) and 2 budgets, and 5 000 spans over the last 7 days. The spans go through the real ingestion pipeline and the hourly rollups are built with the same code the worker uses, so a pass reads rollups for the whole hours of a window and raw spans for the rest, as in production. No rule names a channel, so no notification is queued: the test measures evaluation, not delivery. `spanlight alerts evaluate` then runs one real pass in the worker container (the same function as the job, with one metric cache) and prints the rules evaluated by outcome, the transitions and the seconds. The pass is real and not a dry run because the commit of each rule is part of its cost. The worker's own evaluation job is switched off so it does not take rules from the timed pass. The job runs three passes and takes the median; it fails above 10 seconds, when fewer than 1 000 rules were evaluated, or when any rule or project errored. See [`backend/load/alerts.md`](../backend/load/alerts.md) for running it by hand.

**Result.** Pending: recorded by the `alerts-eval` job of the load-smoke workflow ([`load-smoke.yml`](../.github/workflows/load-smoke.yml)). The run's date, commit, node, PostgreSQL version and the three passes will be written here when it has produced them.

## Trace list sorts, previews and histogram (UX Phase 3)

### Sorts

The trace list sorts by `duration`, `cost`, `tokens` and `retry_cost_usd` in either direction, with unknown values last (see [API deviations](api-deviations.md)). No index backs these sorts, on purpose: Postgres reads every trace of the window from the `(project_id, started_at)` index or the table, and keeps the page with a top-N heapsort, so the time grows with the number of traces in the window and not with the page or its position. A keyset page costs the same as the first page. `started_at` reads the index in order and stops after one page in either direction.

**Budget.** `EXPLAIN (ANALYZE, BUFFERS)` of the first page and of a keyset page at most 150 ms each over a 30-day window, and the request p95 at most twice that of the `started_at` sort under [`traces.js`](../backend/load/traces.js), which sends one request in ten sorted by each of `duration`, `cost`, `tokens` and `retry_cost_usd`. A sort that misses it is removed from the API and the dashboard.

**Method.** [`backend/load/seed_trace_sorts.sql`](../backend/load/seed_trace_sorts.sql) writes 300 000 traces into one project over 30 days (10 000 a day, the trace count of the `realistic` profile), stored oldest first, plus 50 000 traces of a second project, with unknown values at realistic rates: 8.5 % of the costs, 6 % of the retry costs. It writes `traces` only, which is all these sorts read (88 MB). Each query is the page query of `GET /traces` (limit 51) as the application builds it, run as a role without superuser or `BYPASSRLS` and with `app.project_id` set, so row-level security filters it as in production; each was run seven times on a warm cache, and the table gives the median and the slowest in ms. "Keyset page 2" and "keyset page 11" follow the cursors the API returns; "unknown-value boundary" starts at the last known value, so the page is the first page of unknown values. PostgreSQL 17.11 in Docker on a 10-core Apple Silicon laptop (stock settings: 2 parallel workers per query, `shared_buffers` 128 MB, `work_mem` 4 MB), measured on 2026-10-21. "No parallel workers" sets `max_parallel_workers_per_gather = 0`, the case of a server whose parallel workers are all busy.

| Sort             | Order | Page                   | 30 days     | 30 days, no parallel workers | 24 hours    |
| ---------------- | ----- | ---------------------- | ----------- | ---------------------------- | ----------- |
| `started_at`     | desc  | first page             | 0.10 / 0.13 | 0.04 / 0.06                  | 0.10 / 0.12 |
| `started_at`     | desc  | keyset page 2          | 0.11 / 0.12 | 0.04 / 0.05                  | 0.10 / 0.12 |
| `started_at`     | desc  | keyset page 11         | 0.20 / 0.26 | 0.09 / 0.12                  | 0.19 / 0.24 |
| `started_at`     | asc   | first page             | 0.07 / 0.10 | 0.07 / 0.12                  | 0.07 / 0.12 |
| `started_at`     | asc   | keyset page 2          | 0.07 / 0.08 | 0.13 / 3.83                  | 0.08 / 0.09 |
| `started_at`     | asc   | keyset page 11         | 0.07 / 0.10 | 0.20 / 0.30                  | 0.16 / 0.83 |
| `duration`       | desc  | first page             | 38.1 / 38.7 | 101.4 / 116.6                | 3.8 / 4.5   |
| `duration`       | desc  | keyset page 2          | 38.8 / 40.5 | 105.2 / 125.9                | 3.8 / 4.2   |
| `duration`       | desc  | keyset page 11         | 39.1 / 40.4 | 120.2 / 142.9                | 3.7 / 4.3   |
| `duration`       | asc   | first page             | 37.6 / 38.4 | 113.7 / 119.8                | 3.6 / 4.1   |
| `duration`       | asc   | keyset page 2          | 40.6 / 41.8 | 115.5 / 123.4                | 3.5 / 4.4   |
| `duration`       | asc   | keyset page 11         | 41.1 / 51.9 | 121.4 / 132.6                | 3.5 / 4.1   |
| `cost`           | desc  | first page             | 36.6 / 38.3 | 104.5 / 123.4                | 3.3 / 5.1   |
| `cost`           | desc  | keyset page 2          | 39.6 / 42.7 | 112.9 / 117.5                | 3.4 / 4.0   |
| `cost`           | desc  | keyset page 11         | 41.3 / 45.7 | 111.8 / 113.9                | 3.6 / 4.1   |
| `cost`           | desc  | unknown-value boundary | 21.6 / 22.3 | 54.5 / 55.8                  | 2.0 / 2.3   |
| `cost`           | asc   | first page             | 35.4 / 36.0 | 112.6 / 133.5                | 3.6 / 3.8   |
| `cost`           | asc   | keyset page 2          | 37.7 / 39.1 | 126.0 / 136.8                | 3.9 / 4.0   |
| `cost`           | asc   | keyset page 11         | 37.8 / 39.5 | 127.5 / 134.2                | 3.9 / 4.1   |
| `cost`           | asc   | unknown-value boundary | 17.1 / 18.2 | 46.5 / 47.2                  | 1.5 / 2.0   |
| `tokens`         | desc  | first page             | 37.7 / 40.1 | 104.1 / 106.9                | 3.8 / 3.9   |
| `tokens`         | desc  | keyset page 2          | 41.0 / 43.5 | 115.5 / 122.0                | 3.8 / 4.1   |
| `tokens`         | desc  | keyset page 11         | 40.9 / 43.2 | 117.7 / 119.7                | 3.6 / 4.0   |
| `tokens`         | asc   | first page             | 38.5 / 40.2 | 104.9 / 109.4                | 3.6 / 3.9   |
| `tokens`         | asc   | keyset page 2          | 42.6 / 45.3 | 121.7 / 133.0                | 3.9 / 4.6   |
| `tokens`         | asc   | keyset page 11         | 42.1 / 43.3 | 131.6 / 155.9                | 3.8 / 4.5   |
| `retry_cost_usd` | desc  | first page             | 37.9 / 39.5 | 104.1 / 108.2                | 4.3 / 5.7   |
| `retry_cost_usd` | desc  | keyset page 2          | 40.1 / 41.4 | 117.7 / 123.3                | 4.6 / 5.3   |
| `retry_cost_usd` | desc  | keyset page 11         | 42.1 / 43.5 | 115.6 / 118.7                | 5.0 / 5.5   |
| `retry_cost_usd` | desc  | unknown-value boundary | 21.4 / 22.5 | 57.8 / 60.1                  | 1.9 / 2.0   |
| `retry_cost_usd` | asc   | first page             | 42.2 / 44.2 | 127.3 / 190.8                | 4.0 / 4.3   |
| `retry_cost_usd` | asc   | keyset page 2          | 46.7 / 54.2 | 137.2 / 156.6                | 3.9 / 4.2   |
| `retry_cost_usd` | asc   | keyset page 11         | 45.7 / 48.2 | 130.8 / 140.7                | 3.8 / 4.1   |
| `retry_cost_usd` | asc   | unknown-value boundary | 18.1 / 18.9 | 45.3 / 52.7                  | 1.5 / 1.5   |

**Result.** Every sort is within the 150 ms budget over 30 days: about 40 ms with the stock two parallel workers and 100 to 140 ms (median) without them; the slowest single run without parallel workers took 191 ms (`retry_cost_usd` ascending, first page), on a laptop under other load. Over the 24 hours the dashboard opens with, a sorted page takes about 4 ms.

`duration` orders by the interval `ended_at - started_at` itself. Ordering by its milliseconds (`EXTRACT(EPOCH FROM …) * 1000`) took 170 to 220 ms for a keyset page without parallel workers, over the budget, so it was not used.

**Request time.** In process (the whole route: key check, rate limit, row-level security, the page query, the summary details and the scores), 80 to 200 sequential requests per sort and direction on the same data, as an API key, on the same laptop under other load (load average 8 to 9 on 10 cores). The figures are the range over three runs, so they vary more than a dedicated runner would:

| Window   | `started_at` p50 / p95 | value sorts p50 / p95 |
| -------- | ---------------------- | --------------------- |
| 24 hours | 11–15 ms / 15–25 ms    | 17–31 ms / 21–85 ms   |
| 30 days  | 11–24 ms / 19–52 ms    | 43–132 ms / 50–208 ms |

Over 24 hours, the window the dashboard opens with and the one `traces.js` asks for, a sorted request takes about 1.5 to 2 times a `started_at` request at the median, the existing `retry_cost_usd` sort included; single p95 values reached 2.8 times on this machine. Over 30 days a sorted request takes three to five times a `started_at` request, which reads one page from the index whatever the window. The p95 condition is decided by the `traces.js` run of the load workflow on its runner: compare the p95 of each `sort-*` row of its variant table with the `first-page` row (at most twice), and record the table here when it has run.

**Watch.** The cost is linear in the traces of the window. A project with ten times the traces of this seed in 30 days (100 000 a day) would take about ten times as long for a 30-day sorted page. An index per sort would fix that and is out of scope for this phase.

### Previews

`GET /traces?preview=true` adds one statement per page: for each of the page's traces, three `LIMIT 1` picks on the spans primary key (the root, the first llm span with an input, the last llm span with an output), then `left(payload::text, 4000)` of the chosen payload, which Python reduces to at most 200 characters. The `::text` cast reads and serializes the whole JSONB, so the cost grows with the size of the payloads picked, not with the window. The cut is taken outside the picks: computed inside them, Postgres serialized every candidate llm payload of the trace before the top-1 sort, and this seed's page took 11 ms instead of 6. For a chat input (`messages`, or a list of messages) the statement serializes only the last user message (`jsonb_path_query_array(…) -> -1`), so a long system prompt or tool list does not fill the 4000 characters; an input without one is cut whole.

**Budget.** The request with `preview=true` at most twice the plain page (`preview=false`), and the previews statement within the 150 ms of the sorts.

**Method.** [`backend/load/seed_trace_previews.sql`](../backend/load/seed_trace_previews.sql), run after the sorts seed, gives the newest 5 000 traces of the measured project four spans each (20 000 spans, 188 MB): a chain root, with its own short input and output on half the traces, and three llm calls whose inputs are chat requests with a 2.4 KB system prompt. One trace in ten has a first call carrying a 300 KB image that TOAST cannot compress, so a page of 50 reads five of them. Same role, `app.project_id`, machine and runs as the sorts, measured on 2026-10-21 (load average 9 to 17 on 10 cores).

| Statement for a page of 50 (`started_at` desc), median / slowest ms | 24 hours, page 1 | 24 hours, page 11 | 30 days, page 1 | 30 days, page 11 |
| ------------------------------------------------------------------- | ---------------- | ----------------- | --------------- | ---------------- |
| page query                                                          | 0.08 / 0.09      | 0.12 / 0.14       | 0.05 / 0.06     | 0.12 / 0.14      |
| summary details                                                     | 0.50 / 1.15      | 0.47 / 6.47       | 0.43 / 0.89     | 0.46 / 0.53      |
| previews                                                            | 6.13 / 8.04      | 6.32 / 17.64      | 6.25 / 6.58     | 5.99 / 6.13      |
| previews, no parallel workers                                       | 6.21 / 7.47      | 6.00 / 7.50       | 6.49 / 7.09     | 5.87 / 6.69      |

A page of 50 traces without the large images takes 1.6 ms for the previews statement. On the application side the rows took 12 ms to fetch through Docker's network on this laptop (twice the execution time: 100 strings of up to 4000 characters) and `preview_text` 1.7 ms.

Taking the last user message in SQL was measured against cutting the whole input, both statements interleaved 15 times on the same page under a load average of 22 (so the absolute times are about twice those above), median / slowest ms: the page with images 10.2–12.5 / 19–24 before and 12.1–13.4 / 22–24 after (the matched 300 KB message is copied once more), the page without them 2.7 / 5.0–6.3 before and 2.2–2.3 / 2.8–4.4 after (one message is serialized instead of the whole request).

**Request time.** In process, as the sorts' request time: 150 sequential requests per variant, two variants each of `preview=false` and `preview=true` per window, three runs, p50 / p95 in ms:

| Window   | `preview=false`             | `preview=true`              |
| -------- | --------------------------- | --------------------------- |
| 24 hours | 12–26 / 15–62               | 25–40 / 39–72               |
| 30 days  | 12–25 / 19–48               | 23–43 / 27–75               |

**Result.** The previews statement is far inside the 150 ms. A request with previews adds about 10 to 15 ms at the median: 1.0 to 2.4 times the plain page at the median and 0.6 to 2.5 times at the p95, the spread being this laptop's load. On a seed where one trace in ten carries a 300 KB image the request sits at about twice the plain page, so the budget is met only narrowly; the dashboard requests previews only while an Input or Output column is shown.

**Watch.** A page whose picked payloads are all near the 1 MiB limit reads up to 100 MiB of JSONB; at the serialization rate measured here (about 2 ms per 300 KB) that is about 0.7 s. A last user message longer than 4000 characters of JSON text arrives cut, and its preview is that message's start with the JSON punctuation removed.

### Histogram

`GET /traces/histogram` is one statement: the trace list's filters over the window, grouped by `date_bin` of `started_at` (whole hours or days from a UTC midnight). Unlike the list it reads every trace of the window and returns no page, so its cost is that of a full read of the window plus the grouping. No index backs it, as for the sorts.

**Budget.** The sorts' 150 ms for `EXPLAIN (ANALYZE, BUFFERS)` over a 30-day window.

**Method.** The sorts seed and the previews seed on a scratch database (the previews seed gives the model filter spans to find: 5 000 traces with three `gpt-4o-mini` calls each). The statement as the application builds it, as the same unprivileged role with `app.project_id`, nine runs each on a warm cache, median / slowest ms, measured on 2026-10-21 under a load average of 15 to 21 on 10 cores (about twice the load of the sorts' runs). For scale, a plain `count(*)` of the same 30 days of traces took 50 to 65 ms (median of five runs) on the machine at that time.

| Window, filter          | stock settings | no parallel workers |
| ----------------------- | -------------- | ------------------- |
| 24 hours, none          | 5.5 / 21.4     | 5.9 / 7.5           |
| 24 hours, `model`       | 17.4 / 18.3    | 20.0 / 22.0         |
| 30 days, none           | 247 / 351      | 216 / 254           |
| 30 days, `model`        | 71 / 145       | 171 / 197           |
| 30 days, `status=error` | 37 / 38        | 71 / 84             |

**Result.** Over the 24 hours the trace list opens with, the histogram takes about 6 ms, and every filtered 30-day case is within the budget. A 30-day window without a filter misses it on this machine: the planner cannot estimate how few buckets there are, so it sorts all 300 000 rows (spilling past the 4 MB `work_mem`) in one process instead of aggregating in parallel workers. `date_trunc('day', started_at, 'UTC')` took about 1.5 times as long as `date_bin` and was not used. A larger `work_mem` (hash aggregate, still in one process), a join to `generate_series` of the buckets (parallel, but a sort before the join) and `parallel_tuple_cost = 0` gained 0 to 40 % under the same load and were not kept. The unfiltered 30-day case is to be measured again on a quiet machine; until then it is recorded as over the budget.

**Re-measured 2026-10-24** (load average 6.6 to 7.2 on 10 cores): the unfiltered 30-day case still took 210 ms median and 283 ms slowest over nine warm runs, so the miss was the plan, not the machine; a plain `count(*)` of the same rows took 48 ms. Migration 1017 adds expression statistics on the day bucket (`traces_started_day_bin`) and the histogram now writes that bucket with literal constants so the planner matches it: the planner expects 31 groups instead of about 250 000 and aggregates in parallel without a sort. After 1017 the same case takes **58 ms median and 83 ms slowest**, within the budget. Raising `work_mem` alone gave 156 ms median.

**Watch.** The cost is linear in the traces of the window, as for the sorts.

### Sessions list

`GET /sessions` groups every session trace of the window by `session_id` and keeps one page, so every sort (`last`, `cost`, `turns`, `errors`, `tokens`) and the search cost the same: a full read of the window's session traces plus the grouping, then a top-N heapsort. No index backs them. A second statement reads `user_count` and `last_trace_id` for the page's sessions only, on the session index: computed in the grouping, the distinct count and the ordered `array_agg` stopped Postgres from aggregating in parallel and made it sort every trace on disk, about twice the time (540 ms against 296 ms over 30 days). `cost_p99_usd` is a third statement, the same grouping once more with `percentile_cont`, run on the first page only.

**Method.** The sorts seed on a scratch database, then every trace given a session of five consecutive turns and a user out of 3 000 (`session_id = 'chat-' || (n / 5)`, `external_user_id = 'user-' || ((n / 5) % 3000)`, `n` the trace's position by `started_at` in its project), which makes 60 000 sessions in the measured project over 30 days. The statements as the application builds them, as an unprivileged role with `app.project_id`, `EXPLAIN (ANALYZE, BUFFERS)`, measured on 2026-10-24 under a load average of 28 to 34 on 10 cores (other sessions' test suites), so the absolute times are several times those of a quiet machine. The shipped list (before this phase) and the new statements were run interleaved, nine times each, median / slowest ms, 30 days, first page:

| Statement                                        | stock settings | no parallel workers |
| ------------------------------------------------ | -------------- | ------------------- |
| list before this phase (`last` desc)             | 716 / 882      | 1060 / 1890         |
| new list, `last` desc (page + page details)      | 1066 / 1643    | 1940 / 2249         |
| new list, `cost` desc (page + page details)      | 963 / 3719     | 2120 / 2550         |
| `cost_p99_usd`                                   | 617 / 1711     | 965 / 1374          |

Earlier the same day, under less load, single runs gave 259 ms for the list before this phase and 296 ms for the new grouping with the extra sums (the page details statement is 1 to 2 ms), and 109 ms for `cost_p99_usd`. Over 24 hours (2 000 sessions) every page of every sort took 14 to 36 ms (median), the page details 1 to 2 ms and `cost_p99_usd` 4 to 10 ms.

**Result.** Over 24 hours, the window the Sessions page opens with, the list is within the 150 ms of the sorts. Over 30 days it is not, and was not before this phase: the shipped list already took 259 ms on this seed. Under the load above the new list statements take 1.3 to 1.5 times the shipped one and `cost_p99_usd` about 0.9 times more, so a first page reads about 2.3 times the database time of the shipped list and a later page 1.3 to 1.5 times. Sorting and searching add nothing over the default order.

**Watch.** The cost is linear in the session traces of the window. An index cannot help a grouping over the whole window.

## Core screens in the browser (UX Phase 3)

The budgets of the dashboard's core screens, measured in a real browser against the dev stack (Vite dev server, so React runs its unminified development build and every figure is pessimistic against a production build).

| Surface | Budget | Measured | Result |
| --- | --- | --- | --- |
| Overview, first meaningful paint (warm) | at most 1.5 s at 1440 px | KPI links in the page 359–437 ms after navigation start (first paint 260–328 ms) at load 3 to 4; 455–690 ms at load 10; 843–1322 ms at load 20 | met |
| Trace detail, 5 000 spans | no long task over 200 ms on open; smooth scroll | longest task on open 72 ms (load 3.3), 86 ms (load 3.8), 79 ms (load 14), 169 ms (load 21); 219 and 230 ms in two runs at load 20 to 25. Scroll after **Expand all** (60 steps, about 210 frames): 2 and 14 frames over 16.7 ms (1 % and 6.7 %), none over 50 ms but one at 50.1 ms, no long task, in two runs at load 3 to 4; p95 frame 33 ms with 2 frames at 50 ms and no long task at load 3.3; p95 16.8 ms and none over 50 ms at load 14; p95 33–67 ms with 4 to 21 frames over 50 ms at load 20 to 25 | long-task budget met on a machine that is not saturated, missed under heavy load; scroll not met strictly: 1 to 7 % of frames dropped on a quiet machine |
| Payload render | no text node over 64 KB | none under `<body>`, with the span carrying a 1.4 MB base64 image selected, on both the Messages and Raw JSON tabs. The one hit in the whole document is the dev server's 194 KB inline `<style>` in `<head>`, which a production build serves as a file | met |
| Traces list after 40 × Load more | smooth scroll | 2 000 rows, 120 steps: 14 of 392 and 16 of 388 frames over 16.7 ms (3.6 % and 4.1 %), 6 and 2 over 50 ms, longest task 81 and 76 ms, in two runs at load 3 to 4; earlier, p95 frame 16.8 ms, no frame over 50 ms, longest task 61 ms (load 3.2), and 5 of 389 frames over 50 ms (load 11); frames over 16.7 ms were counted only in the two later runs | not met strictly: about 4 % of frames dropped, a few long tasks of 60 to 80 ms during the scroll |
| Session with 100 turns | one turns request | one `GET …/sessions/{id}/turns?limit=100` in every run | met |
| Trace list sorts, previews, histogram | spec ruling 2 | see [Trace list sorts, previews and histogram](#trace-list-sorts-previews-and-histogram-ux-phase-3) | as recorded there |

**Method.** A Playwright script (Chrome 155, 1440 × 900, reduced motion) creates a throwaway workspace and sends its data through `POST /v1/traces` in batches of 1 000 spans: one trace of 5 000 spans (a chain root, 49 chain steps of 101 alternating `llm` and `tool` children, and one `llm` span whose input is a 1.4 MB base64 image; a healthy trace this big opens folded to its root), 2 100 single-span traces, and 100 single-span traces in one session. A `PerformanceObserver` installed before the page's own scripts records long tasks (`longtask`), largest contentful paint and the moment the KPI ribbon's first link enters the DOM. Scroll smoothness is the interval between `requestAnimationFrame` callbacks while the mouse wheel scrolls 400 px every 16 ms (60 steps over the expanded trace, 120 over the list); at 60 Hz a frame is 16.7 ms, so a p95 of 16.8 ms means no dropped frame in 95 % of them, and a frame over 16.7 ms (counted as over 17 ms, for timer jitter) is a dropped one. The text-node check is the spec's script, reporting each element it matches. The overview figure is the second to fourth of four loads of the page. Requests are counted with Playwright's `request` event. Measured on 2026-10-24 on a 10-core Apple Silicon laptop shared with other test suites; the load average next to each figure is the one-minute value read just before that surface was measured.

**What the measurement changed.** With 2 000 rows loaded the traces page rendered the phone's tile list hidden beside the table: 26 019 DOM nodes, and every **J** in the peek re-rendered 2 000 tiles (20 steps: 31 long tasks, the longest 931 ms, at load 20). The page now renders only the layout the screen width shows: 1 411 nodes, and 20 peek steps took 2 to 4 long tasks, the longest 103 to 111 ms (load 3 to 11). Before the change the list scroll gave a p95 of 33 ms with 3 of 390 frames over 50 ms at load 3.8, and 66 ms with 23 of 386 at load 20.

**Watch.** The trace page's open cost is parsing and building the tree of all 5 000 spans in one task; on a saturated machine it crosses 200 ms. Splitting that work is the next step if a production build on a normal laptop ever does too.

## Known slower paths

These are real requests that the scripts do not send yet, so no threshold covers them.

- **Overview, time series or model table with an `environment` filter, for a window of 24 hours or less.** The environment belongs to the trace, so the query joins `traces` to the spans of the window and reads every trace of the project to do it. Without the filter the query reads the spans alone, from the index. In a test database of 2 million spans the filtered 24-hour overview took about four times as long as the unfiltered one, and the gap grows with the number of traces. Windows over 24 hours read the hourly rollups and are not affected. The overview script never sends `environment`, and there is no threshold for this path yet.

## Reading the numbers

- The seeded data is one project, so the figures say nothing about many busy projects at once.
- The seeder ends with `VACUUM ANALYZE`, so every page of the table is marked all-visible and the dashboard's index-only scans never visit the table. A live system gets there through autovacuum, and the migration that added the covering index also makes autovacuum visit `spans` after every 1 % of new rows, so recent pages are marked soon after they are written. A project that ingests faster than autovacuum keeps up will read the table for the newest rows.
- Ingestion is measured at a steady rate. A burst above 50 requests a second per key is limited by design and answered with `429` and `Retry-After`; see the [ingestion spike runbook](runbooks/ingestion-spike.md).
- Percentiles of the 7-day and 30-day windows are estimated from histograms and can be off by up to a quarter of the value; the latency of the request is exact either way.
- For capacity beyond what one stack handles, see [Scale Spanlight](runbooks/scale.md).
