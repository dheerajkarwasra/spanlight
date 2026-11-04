# Performance

How fast Spanlight ingests and serves traces, how that is measured, and how to repeat the measurement. The numbers come from a load test that anyone can run against the Compose stack; this page records the method and, once a run has produced them, the results.

**Current measured baseline:** the [10-million-span Linux campaign](#final-load-campaign-at-10-million-spans-2026-11-02) records both passing paths and capacity limits. Earlier sections retain their original hardware, dates and methods; an unfilled historical workflow table does not mean the later Linux campaign was not run. No 100-million-span capacity result is established here.

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
- **Overview.** The window rotates through 1 hour, 24 hours, 7 days and 30 days. The hour reads the raw spans and gives exact percentiles. The others read their whole hours from the hourly rollups and the recent tail from the raw spans, and say `"approximate": true`. Two checks in the script confirm that each response reports the source its window length calls for (`approximate`), and that the window has data, which for the 7-day and 30-day windows means the rollups hold rows. The p95 of each window is shown in the results but is not a threshold; the threshold is the p95 over all requests.
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

**On GitHub.** Open the Actions tab, choose **Load test** ([`load-smoke.yml`](../.github/workflows/load-smoke.yml)) and **Run workflow**. The profile `realistic` (the default) seeds 1 million spans, about 33 000 LLM calls a day for 30 days, which is a small production team, and runs every scenario for 1 minute. The profile `smoke` seeds 100 000 spans and runs every scenario for 30 seconds, to check the setup. The profile `full` is the stress run: it seeds 10 million spans and runs each scenario for 2 minutes (ingestion for 3), and takes several hours, almost all of it seeding; the seeder prints its rate and stops early if it projects the seeding to overrun its time budget. After the three scenarios on the seeded data it runs the three stages of [reads during ingestion](#reads-during-ingestion), then the gateway overhead scenario. The run's summary page shows the environment (date, runner, versions, number of spans seeded) and a table for each script, the raw k6 summaries are attached as an artifact, and the job is red if any threshold was missed.

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

The original GitHub-hosted `full` profile has no result recorded here. A later, separate [Linux campaign at 10 million spans](#final-load-campaign-at-10-million-spans-2026-11-02) is recorded below; its hardware and workload differ from this runner.

## Reads during ingestion

The scenarios above run one after another, so none of them shows what the dashboard feels like while spans are arriving. [`backend/load/mixed.js`](../backend/load/mixed.js) runs ingestion and reads at the same time.

**Method.**

- **Three scenarios in parallel** in one k6 run, each `constant-arrival-rate` for the profile's read duration: `ingest` sends `INGEST_RPS` requests a second of 100 spans (the request of `ingest.js`, with the seeded ingest key), and `overview` and `traces` each send `READ_RPS` requests a second, 10 by default (the requests of `overview.js` and `traces.js`: the four overview windows ending at `data_end`, and the trace list's first page, filters and sorts, spread over the ten read keys). The two read scenarios together make 20 reads a second, the rate of one read script run alone.
- **Thresholds per scenario**, the same as each script alone: ingestion p95 below 200 ms, overview and trace list p95 below 300 ms, under 1 % failed requests, more than 99 % of checks passed, and no dropped iteration in any scenario.
- **Three stages**, each a separate k6 run with [`settle.sh`](../backend/load/settle.sh) before it, written to `mixed-<stage>.json` and `mixed-<stage>.md`:
  - `baseline`: no ingestion, so the reads alone at the same rate;
  - `half`: 5 requests a second, 500 spans a second, the ingestion target above;
  - `near-knee`: 10 requests a second, 1 000 spans a second, about twice the target. It is reported and not gated: its latency on a shared runner varies from run to run, and its purpose is to show the trade-off, not to fail the job.
- **Profiles.** Every profile runs the three stages after the trace list script. With `full`, the rows are also the query stage at 10 million stored spans.

**Original workflow results.** This CI-runner table remains unfilled. The later [10-million-span Linux campaign](#final-load-campaign-at-10-million-spans-2026-11-02) includes mixed-load results on different hardware. Fill this table only from the workflow and runner described here, with the run's commit, runner, PostgreSQL and k6 versions, spans stored, trace size and retention. Figures are never extrapolated from a smaller run or copied between environments.

| Stored spans | Stage     | Ingest (requests/s · spans/s) | Overview p50 / p95 | Trace list p50 / p95 | Ingest p95 | Failed % | Dropped |
| ------------ | --------- | ----------------------------- | ------------------ | -------------------- | ---------- | -------- | ------- |
| 1 M          | baseline  | 0 · 0                         | not yet run        | not yet run          | –          |          |         |
| 1 M          | half      | 5 · 500                       | not yet run        | not yet run          |            |          |         |
| 1 M          | near-knee | 10 · 1 000                    | not yet run        | not yet run          |            |          |         |
| 10 M         | baseline  | 0 · 0                         | not yet run        | not yet run          | –          |          |         |
| 10 M         | half      | 5 · 500                       | not yet run        | not yet run          |            |          |         |
| 10 M         | near-knee | 10 · 1 000                    | not yet run        | not yet run          |            |          |         |
| 50 M         | all three | not yet run                   |                    |                      |            |          |         |

The columns: the spans stored when the stage ran; the stage; the ingest rate held, in requests and spans a second; the median and p95 of the overview and of the trace list requests; the p95 of the ingest requests (none in `baseline`); the share of failed requests over all three scenarios; and the dropped iterations. Below the table, each stage's markdown file also gives the overview p95 per window and the trace list p95 per variant.

**50 million spans.** About 96 GB of data, which does not fit the disk of a hosted standard runner, and seeding it takes longer than a job may run. This stage needs a self-hosted or larger runner, and it is marked "not yet run" until one has run it; its row will name that runner's hardware.

All of these figures are for one `api` replica on 4 shared vCPU, with the load generator on the same cores.

## Alert evaluation

The [`evaluate_alerts` job](runbooks/alerts.md) runs once a minute and evaluates every enabled rule and budget of every project. The target is that a pass over 1 000 of them finishes in under 10 seconds, so that a worker has most of the minute to spare.

**Method.** [`backend/load/seed_alerts.py`](../backend/load/seed_alerts.py) creates 50 projects, each with 18 rules (threshold and anomaly, over every metric, with windows from 15 minutes to a day, some filtered by environment, provider or model, and several that share a metric, window and filters so the per-pass metric cache matters) and 2 budgets, and 5 000 spans over the last 7 days. The spans go through the real ingestion pipeline and the hourly rollups are built with the same code the worker uses, so a pass reads rollups for the whole hours of a window and raw spans for the rest, as in production. No rule names a channel, so no notification is queued: the test measures evaluation, not delivery. `spanlight alerts evaluate` then runs one real pass in the worker container (the same function as the job, with one metric cache) and prints the rules evaluated by outcome, the transitions and the seconds. The pass is real and not a dry run because the commit of each rule is part of its cost. The worker's own evaluation job is switched off so it does not take rules from the timed pass. The job runs three passes and takes the median; it fails above 10 seconds, when fewer than 1 000 rules were evaluated, or when any rule or project errored. See [`backend/load/alerts.md`](../backend/load/alerts.md) for running it by hand.

**Result.** Pending: recorded by the `alerts-eval` job of the load-smoke workflow ([`load-smoke.yml`](../.github/workflows/load-smoke.yml)). The run's date, commit, node, PostgreSQL version and the three passes will be written here when it has produced them.

## Trace list sorts, previews and histogram (UX Phase 3)

### Sorts

The trace list sorts by `duration`, `cost`, `tokens` and `retry_cost_usd` in either direction, with unknown values last (see [API deviations](api-deviations.md)). No index backs these sorts, on purpose: Postgres reads every trace of the window from the `(project_id, started_at)` index or the table, and keeps the page with a top-N heapsort, so the time grows with the number of traces in the window and not with the page or its position. A keyset page costs the same as the first page. `started_at` reads the index in order and stops after one page in either direction. Since 2026-11-03 a value sort's page query is late-materialized: an inner query sorts only `trace_id`, `started_at` and the value and keeps the page, and the page's rows are then read by primary key, so the window's sort no longer carries whole trace rows. `started_at` keeps selecting whole rows, since it reads one page. The table below was measured before that change.

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

## Exact experiment comparisons

The paired McNemar and sign tests calculate exact binomial probabilities. Previously each
coefficient was computed independently with `math.comb`; the current implementation reuses
adjacent coefficients with integer arithmetic and preserves the exact `Fraction` result.

Measured on 2026-10-29 with Python 3.13.9 on an Apple Silicon Mac, comparing `66957cfa` with
the working-tree optimization. Three repetitions per input, before and after interleaved in
one process, with exact equality checked each time. Other local workloads were active; these
are CPU calculation medians, not API response times or application-wide speedups.

| Discordant pairs (`n`) | Smaller side (`k`) | Before | After |
| --- | --- | --- | --- |
| 1,000 | 450 | 7.93 ms | 0.17 ms |
| 10,000 | 4,500 | 3,495.84 ms | 10.65 ms |
| 10,000 | 5,000 | 3,595.58 ms | 10.75 ms |

The measured function is `app.evals.stats.exact_binomial_two_sided(k, n)`. The public paired-test
regression in `backend/tests/evals/test_paired_stats.py` checks a 10,000-pair comparison against
a generous 500 ms CPU budget alongside exact significance and rounding examples.

## Budget admission

A gateway call under an enabled `block` budget reserves its estimate before it is forwarded (ADR 0017). The reservation locks the budget's counter row and, under that lock, sums the period's priced spans of the budget's scope (`app.budgets.history.read_history`). The gate: with 1 million spans in the budget's month, a call under the budget may add less than 5 ms to the p95 of the same call without one, at 50 requests a second, with no `5xx`.

**Method.** `seed_gateway.py --budget-spans 1000000` adds a second gateway key, 1 000 000 priced `gpt-4o-mini` spans of that key (100 000 traces) spread from the start of the UTC month to the seeding time, and one enabled monthly `block` budget of $1,000,000 on that key, which never refuses, raises the project's retention to 31 days (a monthly budget needs that much retained history; the default is 30), then runs `VACUUM ANALYZE spans`. `gateway.js` with `GATEWAY_SCENARIOS=gateway,budget` sends the gateway request with the plain key, then the same request with the budget key; the figure is `budget_overhead_ms`, the p95 of `budget` minus the p95 of `gateway`. A second run sets `FAKE_PROVIDER_DELAY_MS=1000` and `GATEWAY_VUS=32` (32 virtual users in a loop instead of a fixed rate), to see how many calls a second the budget lets through when calls overlap.

**Machine.** Measured on 2026-11-01 on an Apple M4 Mac (10 cores, 16 GB), Docker Desktop 29.9.2 with 5 CPUs and 6.4 GB for the VM, PostgreSQL 17.11 with the shipped settings, k6 1.7.1, at commit `5342f263` plus the uncommitted working tree (migration 1031 included). Other work ran on the machine; the 1-minute load average is given with each run.

**Result: the gate fails by two orders of magnitude, and a covering index does not fix it.** The index was tried and reverted: `spans_budget_history_idx ON spans (project_id, started_at) INCLUDE (cost_usd, trace_id, span_id, source_key_id, model) WHERE NOT usage_rollup AND cost_usd > 0`, plus `AND s.cost_usd > 0` in the spans term, measured as a trial migration and then removed. The rows marked "trial index" are that build; it is not in the schema.

| Run | Load avg (start → end) | `gateway` p50 / p95 | `budget` p50 / p95 | `budget_overhead_ms` | Failed `gateway` / `budget` |
| --- | --- | --- | --- | --- | --- |
| 50 req/s, shipped schema | 2.30 → 3.92 | 5.1 / 35.5 ms | 1.0 / 703.5 ms | **668.0** | 0.3 % / 76.2 % (`503`) |
| 50 req/s, trial index (reverted) | 3.04 → 4.35 | 6.0 / 45.5 ms | 1.1 / 899.9 ms | **854.3** | 0.7 % / 81.6 % (`503`) |

The failed requests are `503` answers of the gateway's admission cap (`GATEWAY_MAX_IN_FLIGHT`, 8 per process): calls under the budget wait for the counter row one after another, the eight slots fill, and the rest are refused at once, which is also why the `budget` median is about 1 ms. The `gateway` p95 is over its own 25 ms threshold in both runs, on a machine that was not idle; that figure is not part of this gate.

With a provider that takes 1 second and 32 virtual users per scenario, 60 s each:

| Run | Load avg (start → end) | `gateway` p50 / p95 · answered/s | `budget` p50 / p95 · answered/s | p95 difference | Failed `gateway` / `budget` |
| --- | --- | --- | --- | --- | --- |
| Shipped cap of 8 in flight, shipped schema | 2.17 → 6.14 | 3.0 / 5.8 ms · 7.3 | 6.3 / 14.8 ms · 3.7 | 9.1 ms | 99.9 % / 99.9 % (`503`) |
| `GATEWAY_MAX_IN_FLIGHT=64`, shipped schema | 3.47 → 4.17 | 1 124 / 1 223 ms · 28.6 | 3 066 / 3 909 ms · 10.7 | 2 687 ms | 0 % / 0 % |
| `GATEWAY_MAX_IN_FLIGHT=64`, trial index (reverted) | 3.77 → 3.32 | 1 133 / 1 273 ms · 28.3 | 2 942 / 4 039 ms · 10.9 | 2 766 ms | 0 % / 0.3 % (`503`, pool timeout) |

With the shipped cap, 32 overlapping one-second calls are mostly refused whatever the budget does: 8 slots hold about 8 calls a second. With 64 slots the plain key answers about 28 calls a second, and the budget key about 11: the history read runs one call at a time under the counter lock, so one budget admits about 1 / 0.09 s calls a second whatever the provider's latency, and the waiting calls hold database connections (the two `503` answers in the last run were pool timeouts).

**The history read.** `EXPLAIN (ANALYZE, BUFFERS)` of the `read_history` statement for that budget, run as the application role with the project bound, after `VACUUM ANALYZE`, data in cache. The spans term dominates. The reservation term took 5 ms with 735 settled reservations and 13 ms with about 2 900, since it grows with the budget's calls in the period; the deleted-span term took 0.01 ms.

```text
Shipped schema: Execution Time 115.6 ms (117 to 173 ms on repeats)
  Finalize Aggregate (actual time=110.280..110.401 rows=1)  Buffers: shared hit=30296
    Gather  Workers Launched: 2
      Hash Anti Join (actual rows=333333 loops=3)  Hash Cond: (s.trace_id = r.trace_id AND s.span_id = r.span_id)
        Parallel Seq Scan on spans s (actual time=0.015..59.233 rows=333578 loops=3)  Buffers: shared hit=30166

Trial index (reverted): Execution Time 101.5 ms (98 to 241 ms on repeats)
  Finalize Aggregate (actual time=87.880..88.034 rows=1)  Buffers: shared hit=17320
    Gather  Workers Launched: 2
      Hash Anti Join (actual time=0.729..70.378 rows=333333 loops=3)
        Parallel Index Only Scan using spans_budget_history_idx on spans s (actual time=0.016..39.904 rows=333333 loops=3)  Heap Fetches: 0  Buffers: shared hit=16956
```

The index removed the heap visits (about 30 000 buffers down to 17 000) but not the work: the read still aggregates every priced span of the month and anti-joins it with the budget's reservations, about 0.1 µs a span, so it grows with the month's traffic. It saved about 15 % (115 ms to 100 ms steady) for 133 MB of index per million spans, written on every ingested span, so it was reverted. Ingestion met its target while the index was in place (`ingest.js`, 60 s: p95 133.7 ms, 0 % failed, load average 3.81 → 2.78; an earlier attempt at load average 7.9 was discarded). The next remedy, closed-day snapshots per budget, changes ADR 0017's accounting and needs its own decision.

**Hourly snapshots (migration 1032).** Admission now sums the period's closed UTC hours from per-budget snapshot rows (`budget_bucket_snapshots`, valid while their generation matches the hour's `budget_bucket_versions` row, which statement triggers on `spans` and `budget_reservations` bump) and scans only the live tail: the open hour plus the 12-minute lease (`docs/specs/2026-11-01-budget-day-snapshots.md`, ADR 0017 amendment of 2026-11-01). The total is proven equal to the full period scan by `backend/tests/budgets/test_snapshot_equality.py`. Measured on 2026-11-01 (the first of the month, so the seeder's 1 000 000 spans fell into 12 hours, about 81 000 an hour), same machine, at commit `5342f263` plus the uncommitted working tree; `budget_overhead_ms` now compares successful calls only and any failed `budget` call fails the figure.

| Run | Load avg (start → end) | `gateway` p50 / p95 | `budget` p50 / p95 | `budget_overhead_ms` | Failed `gateway` / `budget` | Dropped |
| --- | --- | --- | --- | --- | --- | --- |
| 50 req/s, seed as made (about 22 000 seeded spans in the open hour) | 1.83 → 2.71 | 4.6 / 6.3 ms | 473 / 2 349 ms | **2 345** | 0 % / 3.2 % (92 × `503`, pool timeouts) | 146 |
| 50 req/s, seeded spans moved out of the open hour (a month-even seed) | 1.47 → 2.37 | 4.5 / 5.7 ms | 20.0 / 86.1 ms | **80.4** | 0 / 0 | 0 |

With a provider that takes 1 second, 32 virtual users per scenario and `GATEWAY_MAX_IN_FLIGHT=64` (the run the shipped cap of 8 refused before; the shipped default is now 512):

| Run | Load avg (start → end) | `gateway` p50 / p95 · answered/s | `budget` p50 / p95 · answered/s | p95 difference | Failed |
| --- | --- | --- | --- | --- | --- |
| Hourly snapshots | 1.90 → 2.39 | 1 134 / 1 219 ms · 28.5 | 1 059 / 1 247 ms · 29.5 | 28 ms | 0 / 0 |

**Result: throughput is fixed, the 5 ms overhead at 50 requests a second is not.** The budget key now admits at the gateway's own rate (29.5 against 28.5 calls a second, from 10.7 against 28.6), with no `503`, and the second 50 req/s run has zero failures and zero dropped iterations where the shipped schema had 76 % failures. The 12 closed hours cost 0.27 ms a call from their snapshots (statement (1), `generate_series` left-joined to both tables, 245 buffers) and were computed once, in one pass. What remains is the live tail, which grows with the calls of the open hour: the budget scenario's own calls. `EXPLAIN (ANALYZE, BUFFERS)` of the live statement at the end of the second 50 req/s run, with 3 021 settled reservations admitted in the open hour:

```text
Execution Time 38.5 ms (statement (3), live range [12:00, 2026-12-01), gateway_key scope)
  spans term:        Hash Anti Join (actual rows=0)  8.7 ms
    Index Scan using spans_project_gateway_started_idx (actual rows=3021)   the run's own gateway spans
  reservation term:  Nested Loop Left Join (actual rows=3021)  28.9 ms
    Seq Scan on budget_reservations r (actual rows=3021)
    Index Scan using spans_pkey on spans s (actual time=0.009 rows=1 loops=3021)   one probe per settled call
  ledger term:       0.02 ms (no rows)
```

Each settled call of the open hour costs about 9 µs (its span probe) plus about 3 µs (its span in the uncorrelated scan) on every later admission until the hour closes, so the locked section grows from about 1 ms at the start of a run to about 40 ms after 3 000 calls; at 50 calls a second that queue is what the p50 of 20 ms and the p95 of 86 ms show. The first run adds the seeded spans of the open hour on top (about 22 000, 10 ms, and more as the hour fills), which is the seed's shape on the first of the month rather than the design. The reservation and span terms of the open hour are bounded by the hour's call count, not by the month's spans, so a budget that admits a few calls a second stays well under a millisecond per call, while a busy budget still serialises on its own recent calls: a counter-based open hour, or a finer bucket for it, is the next decision.

**Ingest upkeep.** The span triggers run on every span write. `ingest.js` for 60 s on a freshly migrated stack: without a budget on the project the trigger returns after one `EXISTS` on `budgets` (p50 65.5 ms, p95 127.4 ms, p99 293.1 ms, load average 4.95 → 2.79); with a monthly `block` budget on the project it also upserts the version row of each touched hour (p50 64.5 ms, p95 83.6 ms, p99 132.0 ms, load average 2.39 → 1.80). Both under the 200 ms threshold, 0 % failed, nothing dropped; the Task 3 figure on this tree was about 115 ms. With the `traces` trigger and the batch's own sorted pre-bump (`app.budgets.buckets.bump_hours_of_traces`, which replaces the old-hours read, so the statement count of a batch is unchanged), the same run with a block budget on the project: p50 67.0 ms, p95 121.2 ms, p99 400.1 ms, load average 3.11 → 2.44, 0 % failed, nothing dropped, no deadlock.

**Running hour total (migration 1033).** Every hour of the period is now a snapshot row, the open one included; a writer that can price its change exactly appends it to `budget_bucket_deltas` (an uncorrelated span stored or corrected, a settled call's charge), and admission folds the pending deltas of the hours it reads into their rows under the row lock in one statement (`app.budgets.compaction`), so nothing it runs grows with the calls or spans of the open hour (spec §8, ADR 0017 amendment "running hour total"). Changes a writer cannot price (a deleted span, a reaped or reclassified call, a trace-field change) still bump the hour's version, and the next admission recomputes that hour once. Measured on 2026-11-01 on the same machine (seeded at 13:50 UTC on the first of the month: 1 000 000 spans over 14 hours, about 72 000 an hour, before the budget existed), at commit `5342f263` plus the uncommitted working tree. Compose project `spanlight-load-w0-2c`, torn down with its volumes afterwards.

| Run | Load avg (start → end) | `gateway` p50 / p95 | `budget` p50 / p95 | `budget_overhead_ms` | Failed | Dropped |
| --- | --- | --- | --- | --- | --- | --- |
| 50 req/s for 60 s, open hour fresh (3 000 calls by the end) | 2.23 → 2.00 | 4.3 / 5.6 ms | 10.0 / 14.0 ms | **8.3** (median difference 5.7) | 0 / 0 | 0 |
| 50 req/s for 420 s started at 14:00:15, `budget` only (21 021 settled calls in the open hour by the end) | 1.74 → 3.04 | 5.5 / 6.7 ms (a 420 s `gateway` run on the same stack, 14:08) | 11.5 / 41.0 ms, p90 14.1 | **34.3** | 0 | 0 |
| 50 req/s for 60 s, open hour holding 24 000 settled calls | 2.84 → 3.23 | 5.3 / 7.0 ms | 10.7 / 51.9 ms, p90 18.1 | **44.8** | 0 / 0 | 0 |
| 50 req/s for 420 s started at 15:00:12, `budget` only, final build (21 020 settled calls in the open hour by the end) | 1.42 → 2.77 | 5.5 / 6.7 ms (same reference) | 9.8 / 47.3 ms over the run; **8.6 / 11.5 ms over its last four minutes** (9 000 to 21 000 calls in the hour), p99 41 to 50 ms | **40.6** over the run, **4.8** over the last four minutes | 0 | 1 (at 15:00, the worker's five-minute jobs) |

With a provider that takes 1 second, 32 virtual users per scenario and `GATEWAY_MAX_IN_FLIGHT=64`:

| Run | Load avg (start → end) | `gateway` p50 / p95 · requests | `budget` p50 / p95 · requests | Failed |
| --- | --- | --- | --- | --- |
| Running hour total | 2.15 → 2.61 | 1 118 / 1 218 ms · 1 732 | 1 045 / 1 262 ms · 1 785 (103 %) | 0 / 0 |

**Result: the open hour no longer costs anything per call; the 5 ms p95 gate is met once the planner's statistics are fresh, and missed before that for a reason outside the history read.** The last run tells both halves: for its first three minutes the budget median was 11.5 ms and the p95 45 to 135 ms; at 15:02:52 autovacuum analysed `spans` for the first time since the seed (the seeder's `VACUUM ANALYZE` ran before any gateway span carried a `request_hash`), the recorder's trace-total statement (below) switched from the partial request-hash index to `spans_pkey` (69 ms to 0.18 ms), and for the remaining four minutes, with the open hour filling from 9 000 to 21 000 calls, the budget path ran at p50 8.6 ms, p90 9.8 ms, p95 11.5 ms per minute: an overhead of 3.1 ms at the median and 4.8 ms at p95 against the 420 s `gateway` run, flat while the hour filled. Over the 420 s run the median admission moved from 10.1 ms in its first minute to 11.1 ms in its seventh while 21 000 settled calls accumulated in the open hour; no hour was ever recomputed (`budget_bucket_versions` stayed empty: the gateway's own span writes and settles are deltas, not bumps) and the delta table held one or two rows at any moment (autovacuum kept it at 16 kB). `EXPLAIN (ANALYZE, BUFFERS)` as the app role at the end of that run: statement (1), the hours of the period with their row, version and pending deltas (16 rows from `generate_series`, index probes on the three bucket tables), 0.34 ms, 342 buffers (0.39 ms and 12 buffers at 3 000 calls; the difference is the hot rows' version chains); the compaction statement (row lock, delete of the hour's deltas, fold, update), 0.37 ms, 16 buffers (0.49 ms at 3 000 calls). Neither plan has a scan whose rows grow with the hour. The old live statement read 38.5 ms at 3 000 calls and would have read about 270 ms at 21 000.

What remains is queueing on the admission's critical section, which is now about five milliseconds of round trips (the guard's budget lock, the counter lock, the two history statements, the counter update, the reservation insert and the commit, then the settle's counter update and commit in the recorder's transaction) and not database work: `log_lock_waits` during a 60 s `budget` run showed admissions queued four deep on the budget row (`guard.py` takes `FOR NO KEY UPDATE OF budgets`; SQLAlchemy's `with_for_update(key_share=True)` without `read=True` is not `FOR KEY SHARE`) for 30 to 75 ms, with no statement of the admission itself over 20 ms. `synchronous_commit = off` for one diagnostic run changed nothing (p95 49.9 ms), so it is not commit latency. During a `budget`-only run the single api process sits at 65 to 78 % of a core and Postgres at 130 to 170 %, a third of which is the recorder's trace-total recompute (`_RECOMPUTE_ROLLUPS` in `app/ingest/store.py`): the planner serves `trace_id = ANY(...)` from the partial `spans_project_request_hash_started_idx` and walks every gateway span of the project, 66 854 buffers and 69 ms for one trace at 27 000 gateway spans, growing with them; that statement is outside this change and is the next thing to fix. The tail is therefore the event loop and the database stalling inside a serialised section at 50 calls a second on one api process, which the plain path, having no serialised section, does not show; with that statement planned well, the section is short enough for the gate. Measuring on a freshly seeded stack: run `ANALYZE spans` after the first gateway calls, or the first minutes measure the stale plan.

**Ingest upkeep with deltas.** `ingest.js` for 60 s on the rebuilt stack, project with a monthly `block` budget: p50 69.0 ms, **p95 93.7 ms** (pass), p99 152.7 ms, 0 % failed, nothing dropped; 300 batches wrote 300 deltas and bumped no version row (the open hour stayed exact), and the reaper's minute job folded them into the hour row (`spanlight_budget_deltas_compacted_total`), as it does for every budget nobody admits against.

## Gateway admission

Gateway HTTP admission (`app.gateway.admission`) admits a call while three per-process limits hold: `GATEWAY_MAX_IN_FLIGHT` calls (default 512), `GATEWAY_MAX_IN_FLIGHT_BYTES` of declared request bodies (default 80 MiB, a chunked or undeclared body counting as the full 10 MiB cap and `GET`, `HEAD` and `OPTIONS` as nothing) and `GATEWAY_MAX_IN_FLIGHT_PER_KEY` calls per caller key (default 256). The earlier single cap of 8 was sized for ~10 MiB bodies (about 32 MB of RSS each) and refused the ninth small stream (about 0.35 MB each). The gate: 200 virtual users of streaming chat against a provider that takes 1 second, 60 s, at the shipped defaults, with no `503`; the peak RSS recorded; and the 50 requests a second overhead threshold still met.

**Method.** `gateway.js` with `GATEWAY_SCENARIOS=concurrency` and `FAKE_PROVIDER_DELAY_MS=1000` on the stack: 200 virtual users in a loop, alternating over the two keys `seed_gateway.py` writes (100 a key), each sending `"stream": true`; the fake provider waits 1 s, then streams three chunks and `[DONE]`. Latency counts successful calls only (a fast `503` would pull a p95 over every response down), and any refused call fails the run. Peak RSS is the api process's `VmHWM` (`/proc/1/status`), the api recreated before each run. Same machine and setup as [Budget admission](#budget-admission), at commit `5342f263` plus the uncommitted working tree.

Shipped defaults (512 calls, 80 MiB, 256 per key):

| Run | Load avg (start → end) | Successes p50 / p95 · answered/s | Refused (`503`) | Peak RSS |
| --- | --- | --- | --- | --- |
| 50 req/s overhead (`gateway,direct`, no delay) | 3.12 → 2.56 | `gateway` 4.3 / 5.7 ms, `direct` p95 2.0 ms; overhead **3.7 ms** (limit 20) | 0 | 181 MiB |
| 200 users | 1.86 → 3.07 | 2 454 / 2 732 ms · 84.2 | **0 of 5 052** | 207 MiB |

**Result: the gate passes.** No call was refused, the overhead stayed at 3.7 ms and the peak RSS was 207 MiB, against 446 MB for eight ~10 MiB bodies in the calibration. The latency is the provider pool's (below), not admission's.

The defaults were first set at 256 calls and 128 per key, which this gate refused:

| Run | Load avg (start → end) | Successes p50 / p95 · answered/s | Refused (`503`) | Peak RSS |
| --- | --- | --- | --- | --- |
| 200 users, 256 / 128 | 1.72 → 2.16 | 2 449 / 2 742 ms · 83.4 | **95 of 5 099** (84 `key`, 11 `count`) | 207 MiB |
| 200 users, 256 / 128, start times spread over 2.4 s | 2.06 → 2.80 | 2 451 / 2 727 ms · 83.4 | **110 of 5 114** | 208 MiB |
| 200 users, 512 / 256 | 2.31 → 2.42 | 2 444 / 2 789 ms · 84.6 | 0 of 5 076 | 207 MiB |

200 users never hold more than 200 calls from the client's side, but a slot lasts until the call's finalization ends, which runs after the last byte has been sent: a probe build measured that tail at p50 28 ms, p95 44 to 68 ms, max 141 ms. The provider HTTP pool (`app.gateway.http.POOL_SIZE`, 100 connections a process) serves the 200 streams in two batches, so calls finish in waves of about 100 (upstream time averaged 1.86 s for a 1 s provider, hence the 2.4 s latency); during a wave each user's next call arrives while its last one is still finishing, and the probe saw in-flight calls reach 256 and one key reach 128. Spreading the start times does not help, because the pool re-forms the waves. With 512 and 256 the same load is refused nothing, at the same memory, so those became the defaults: the byte budget (200 × about 200 bytes here) is far from full, and RSS stays near the 195 MB calibration for small streams.

## Ingest round trips

Before writing, an ingest batch locks the identity of every trace it touches (`app.ingest.aggregate_changes.lock_identities`), so a concurrent batch or usage correction cannot interleave with it. That lock used to be one `pg_advisory_xact_lock` statement per trace, after a second project bind and `KEY SHARE` that the pipeline had already done, and the dirty hours were read again after the writes. A batch of 100 traces of 2 spans made 115 statements. It now makes 12, whatever the number of traces: all the advisory locks are one statement over the sorted keys (the same lock order as before), the project is bound and share-locked once, and the hours after the writes are derived from the batch (`app.ingest.store.batch_hours`), a superset of what the second read returned. `backend/tests/ingest/test_ingest_round_trips.py` keeps the bound.

**Measured** on 2026-11-01 on an Apple M4 Mac (10 cores, 16 GB), Docker Desktop 29.9.2 with 5 CPUs and 6.4 GB for the VM, PostgreSQL 17 with the shipped settings, k6 1.7.1, on top of commit `5342f263`, before this change was committed: once without it (before) and once with it (after). Each run is `ingest.js` for 60 s on a freshly migrated stack with only a workspace and an ingest key, so no stored spans; before and after alternate in one window. `mixed.js` was not run, so these are ingest-only figures on an empty database. Other work ran on the machine; the 1-minute load average is given with each run.

| Run | Load avg (start → end) | Median | p95 | p99 | Failed |
| --- | --- | --- | --- | --- | --- |
| Before, 1 | 5.92 → 3.62 | 68.9 ms | 132.0 ms | 291.1 ms | 0 % |
| After, 1 | 2.13 → 2.53 | 65.6 ms | 112.2 ms | 300.0 ms | 0 % |
| After, 2 | 2.10 → 1.59 | 65.5 ms | 117.6 ms | 295.7 ms | 0 % |
| Before, 2 | 1.40 → 1.52 | 68.3 ms | 128.6 ms | 300.8 ms | 0 % |

Each `ingest.js` request carries 29 traces (1 to 6 spans each), so the change saves 31 of its 43 statements. At 500 spans a second it takes about 3 ms off the median and 10 to 20 ms off the p95. An empty database flatters both; the realistic-seed comparison is still to be run.

## Speed work, before and after (2026-11-02)

The same local run twice, before and after a set of speed changes (price book and gateway key caching, the trace list's model filter bounded to the window with statistics on `spans (project_id, model)`, the trace-total recompute, read-only gateway cache hits, fewer budget-admission statements, gateway span writes kept below the request pool, the load-test harness fixes): the smoke seed (100 000 spans) plus `seed_gateway.py --budget-spans 1000000`, then every script at its smoke duration with [`settle.sh`](../backend/load/settle.sh) before each, and the gateway pair three times. Apple M4 Mac (10 cores, 16 GB), Docker Desktop with 5 CPUs and 6.4 GB for the VM, PostgreSQL 17 with the shipped settings, k6 1.7.1, one `api` replica; other work ran on the machine (1-minute load average 1.7 to 3.1 before each script). Before: 07:20 UTC, at commit `5342f263` plus the uncommitted tree of the time; after: 10:09 UTC, with the changes. These are local smoke figures, not the `realistic` profile on a CI runner.

| Script | Scope | p50 before → after | p95 before → after | p99 before → after | Thresholds after |
| --- | --- | --- | --- | --- | --- |
| `overview.js` | all | 20.5 → 19.2 ms | 34.2 → 30.0 ms | 236.1 → 56.3 ms | pass |
| `traces.js` | all | 8.6 → 10.9 ms | **59.8 → 26.5 ms** | 94.7 → 29.1 ms | pass |
| `traces.js` | one model | 53.4 → 24.2 ms | 69.2 → 28.0 ms | | |
| `mixed.js` baseline | overview / traces | 26.5 → 31.3 / 12.6 → 14.5 ms | 39.6 → 40.2 / 59.1 → 27.7 ms | | pass |
| `mixed.js` half | ingest / overview / traces | 54.1 → 52.0 / 26.5 → 26.6 / 12.2 → 11.6 ms | **122.7 → 80.0** / 54.1 → 52.2 / 50.2 → 23.4 ms | | pass |
| `ingest.js` | all | 64.6 → 61.7 ms | **131.3 → 74.1 ms** | 195.4 → 125.6 ms | pass |

No read or ingest request failed and no iteration of those scripts was dropped in either run. Before, `overview.js` and both `mixed.js` stages failed their `checks` threshold because the script expected an exact source for the 24-hour window, which the hybrid read reports as approximate; the script now matches the API.

Gateway, median of three passes (each pass: `gateway,direct`, then `gateway,budget`, 50 requests a second for 60 s each):

| | Before | After |
| --- | --- | --- |
| Through the gateway, p95 | 11.9 ms (passes 5.5 to 61.2) | 5.6 ms (4.4 to 10.0) |
| **Overhead** (limit 20 ms) | **9.7 ms** (3.8 to 59.0) | **3.7 ms** (2.5 to 7.4) |
| Budget key, p95 | 13.6 ms | 8.8 ms |
| **Budget overhead** (limit 5 ms) | **7.7 ms**, over the limit in every pass | **3.6 ms** in two passes of four; 272.6 and 364.2 ms in the other two |

The budget overhead is under its limit whenever no stall lands in the `budget` scenario: the admission itself adds about 3.6 ms at p95. In two of four passes a stall of one to three seconds did land there (in one, the request pool's connections were all taken for about a second and 11 calls got `503`), which puts the scenario's p95 at 330 to 370 ms; the stalls line up with no worker job or checkpoint.

**After the budget lock change.** The stalls were a convoy on the budget row: the guard locked it `FOR NO KEY UPDATE` for the whole admission, so every call of one budget waited for the one before it, and the waiters held request-pool connections until the pool ran out (`503` for every request of the process). The guard now holds the budget `FOR SHARE`, and calls queue only on the period's counter row while the reservation is written. A later quiet re-run of the same gateway pair (a fresh load stack on the same seed; 1-minute load average 2.8 to 3.2, a different session from the run above): budget key p95 9.1 / 9.6 / 9.3 ms over three passes, **budget overhead 2.9 / 4.4 / 3.5 ms**, every pass under the 5 ms limit, with no `503` and no request-pool timeout.

`pg_stat_statements` over one `mixed.js` half stage (a diagnostic run, not part of the shipped stack): database time fell from 6 160 to 4 142 ms for about 8 700 statements, and the trace list's model filter, which was half of it (53.5 ms a call, about 124 000 buffers), now takes 6.6 ms a call (2 200 buffers).

## Final load campaign at 10 million spans (2026-11-02)

One production-like campaign on a rented Linux server, to find where a single Spanlight stack stops meeting the targets above and what breaks first. Every request went through Caddy, as in a self-hosted install. No real LLM provider was called: the gateway scenarios use `backend/load/fake_provider.py`.

| | |
| --- | --- |
| Host | Hetzner Cloud CPX62: AMD EPYC Genoa, 16 shared vCPU, 30 GiB RAM, 610 GB local NVMe, Ubuntu 26.04, Docker 29.1 |
| Split | Spanlight (Caddy, `api`, `worker`, Postgres) pinned to cores 0–11; k6 and the fake provider on cores 12–15 |
| Noise | CPU steal recorded every 5 s; mean steal was 0.00 % in every run reported here. Idle disk: 46 800 random 8 KiB IOPS |
| Stack | `deploy/compose.yaml` + `backend/load/compose.load.yaml`, Postgres 17 with the shipped settings plus `pg_stat_statements` (diagnostic), one `api` process unless stated |
| Tools | k6 2.3.0 (`grafana/k6:2.3.0`), the scripts in `backend/load/` |
| Data | `seed.py --spans 10000000`: 10 000 000 spans in 2 862 605 traces over 28 days in one project (`spans` table 25 GB, about 2.5 GB per million spans), then `seed_gateway.py --budget-spans 1000000`, 20 tenant projects with one ingest key each, and `seed_tour.py`; 11 million spans and 27 GB before the runs, 16 million and 35 GB after |

### Baseline: the scripts above at 10 million spans

Three passes of each script, `settle.sh` before each. Ranges are the three passes.

| Script | p95 | Target | Result |
| --- | --- | --- | --- |
| `overview.js` | 74.8–78.2 ms | 300 ms | pass |
| `traces.js` | 76.6–84.5 ms | 300 ms | pass |
| `mixed.js` baseline (no ingestion): overview / traces | 74.4–87.9 / 78.3–90.4 ms | 300 ms | pass |
| `mixed.js` half (500 spans/s): ingest / overview / traces | **308–1 094** / 209–283 / 166–233 ms | 200 / 300 / 300 ms | **ingest fails** |
| `mixed.js` near knee (1 000 spans/s): ingest / overview / traces | **446–560 / 376–459 / 322–385 ms** | 200 / 300 / 300 ms | **fails** |
| `ingest.js` (500 spans/s) | **210–379 ms** | 200 ms | **fails** |
| `gateway.js` overhead | 10.9–14.0 ms | 20 ms | pass |
| `gateway.js` budget admission overhead | **38.6–62.2 ms** | 5 ms | **fails** |

No request failed in any of these runs; the misses are latency.

`tour.js`, 50 requests a second over every other product area for 3 minutes, p95 of three passes: trace detail 507–566 ms, the 5 000-span trace 729–774 ms, sessions list 811–884 ms (all over the script's 500 ms threshold); span detail 386–418, end users 328–382, attribution 295–327, datasets 262–296, experiments 253–301, prompts 266–306, insights 259–294, annotation queue items 257–302, OTLP JSON 392 and protobuf 388, scores 326 ms; experiment compare, runs, dataset items, prompt versions, cost explanation, attribution series and the insights summary 55–110 ms. Login p95 928 ms (one password hash per sign-in). Most endpoints have a median of 30–65 ms; their p95 of 250–400 ms is time spent queued behind the other requests on the one `api` process. No tour request failed.

### Breaking points

60 s steps at a constant arrival rate; ingestion with 100-span requests over 20 project keys, so neither the per-key limit nor the per-project cap is what is measured. Knee: highest step with p95 under 500 ms, under 1 % failures and no dropped iterations.

| Workload | `api` processes | Knee | Break | First limit |
| --- | --- | --- | --- | --- |
| Ingestion | 1 | 1 500 spans/s (p95 451 ms) | 2 000 spans/s: 15.6 % `503`, p95 2.5 s | the `api` process at 97 % of one core; requests then time out waiting for a database connection (`db_pool_timeout`) |
| Ingestion | 3 | 4 000 spans/s (p95 477 ms) | 6 000 spans/s: 14.5 % `503` | the three processes at 85–95 % and lock waits on the project's current-hour `aggregate_dirty_hours` row |
| Gateway, 0 ms provider | 1 | 100 calls/s (p95 460 ms) | 200 calls/s: 53 % failed | the `api` process |
| Gateway, 0 ms provider | 3 | 200 calls/s (p95 45 ms) | 400 calls/s: p95 1.2 s | the `aggregate_dirty_hours` row: 149 ms per insert on average, up to 20 sessions waiting on it |
| Reads (20/s) during ingestion | 1 | with 1 000 spans/s: p95 328–380 ms, no failures | with 2 000 spans/s: p95 1.8–2.0 s, 27–36 % failed | shared `api` process |

**Spike** (1 `api`): 700 → 3 000 → 700 spans/s, 60 s each. During the spike 53 % of requests got a fast `503`; in the two minutes after it no request failed and p95 was back at 265 ms.

**Soak** (1 `api`): 700 spans/s plus 10 dashboard reads a second for 60 minutes. Ingest p95 fell from 366 ms in the first 15 minutes to 288 ms in minutes 32–47, the overview from 313 to 225 ms, the trace list from 1 384 to 589 ms (the cache warming); `api` memory stayed at 292 MB and the worker at 213–218 MB throughout. From minute 47 every ingest request got `429 INGEST_QUOTA_EXCEEDED` with `Retry-After`: the run had used the project's daily byte quota (the cap was raised to 2 GB a day for this run and the 100-span requests carry about 110 KB). Reads were unaffected, and ingestion resumed within a minute of raising the cap.

**Noisy tenant** (1 `api`): ten tenants ingesting 100 spans/s each while one tenant sends 5 000 spans/s, five times its default rate cap of 1 000 spans/s.

| | Alone | With the flooding tenant |
| --- | --- | --- |
| Other tenants' p95 | 311 ms | **2 066 ms** |
| Other tenants' failed requests | 0 % | **48 %** (`503`) |
| Flooding tenant answered `429` (all with `Retry-After`) | — | 37 % |

The per-project cap protects storage, not the `api` process: a refused request still costs a full request's work up to the cap check, and 50 requests a second from one tenant saturate the single process for everyone.

**Faults under load** (1 `api`, 700 spans/s plus reads): a Postgres restart failed 16 requests within one 10-second window, then none. `docker kill` of the `api` and of the worker counts as a manual stop for Docker's `unless-stopped` policy, so neither came back until Compose restarted them (the `api` was healthy about 20 s later); a process crash, which the policy does restart, was not simulated.

### Correctness after the load

- Trace totals equal a recount from their spans for 10 000 random traces: 0 mismatches in span count, error count, tokens or cost.
- During the fault run, k6 received 1 676 successful ingest responses (167 600 spans) between 20:08 and 20:12 UTC, and exactly 167 600 spans were stored for that window. The batches that failed during the Postgres restart stored nothing.
- The same 50-span batch sent twice is stored once and leaves the trace totals unchanged.
- A span with an unknown model has a `NULL` cost, never 0; no priced span with tokens has a cost of 0.
- A read key of one project asking for another project's traces gets `404`.

### What breaks first, and why

1. **Span inserts at 10 million spans.** `INSERT INTO spans` averaged 118 ms per 100-span batch in the half-load mixed run (a few milliseconds at 100 000 spans). The largest index on `spans` is `spans_content_trgm_idx`, a 7.9 GB trigram GIN index for content search; GIN indexes buffer new entries and flush them inside whichever insert fills the buffer, which matches the shape seen (median 139 ms, p95 1.1 s) with idle CPU and no I/O wait.
2. **One `api` process.** Ingestion, reads and the gateway share one process, which saturates at about 1 500 spans/s or 100 gateway calls/s and turns overload into `503` for every tenant. Three processes give 2.7× the ingestion knee.
3. **The current-hour `aggregate_dirty_hours` row.** Every span write of a project updates its row for the current hour. Under gateway load each write waited 51 ms on it (budget run) and 149 ms with three `api` processes; this is what the budget admission overhead and the 3-process gateway limit consist of.
4. **Per-tenant isolation.** Rate and quota caps answer `429` correctly but do not keep one tenant from using up the shared process.

### Not measured

Real provider latency and errors, email delivery, billing fetches from provider APIs, network latency between client and server (k6 ran on the same host), a process crash restarted by Docker, and more than 10 million stored spans.

## Known slower paths

These are real requests that the scripts do not send yet, so no threshold covers them.

- **Overview, time series or model table with an `environment` filter, for a window of 24 hours or less.** The environment belongs to the trace, so the query joins `traces` to the spans of the window and reads every trace of the project to do it. Without the filter the query reads the spans alone, from the index. In a test database of 2 million spans the filtered 24-hour overview took about four times as long as the unfiltered one, and the gap grows with the number of traces. Windows over 24 hours read the hourly rollups and are not affected. The overview script never sends `environment`, and there is no threshold for this path yet. Since 2026-11-02 the filtered reads look up each trace's environment once per trace instead of once per span, 1.4 to 2 times faster on a 35 000-span raw tail; alert rules with a trace filter (environment, user, metadata, trace name) still join per span, about 50 to 80 ms on such a range.

## Reading the numbers

- The seeded data is one project, so the figures say nothing about many busy projects at once.
- The seeder ends with `VACUUM ANALYZE`, so every page of the table is marked all-visible and the dashboard's index-only scans never visit the table. A live system gets there through autovacuum, and the migration that added the covering index also makes autovacuum visit `spans` after every 1 % of new rows, so recent pages are marked soon after they are written. A project that ingests faster than autovacuum keeps up will read the table for the newest rows.
- Ingestion is measured at a steady rate. A burst above 50 requests a second per key is limited by design and answered with `429` and `Retry-After`; see the [ingestion spike runbook](runbooks/ingestion-spike.md).
- Percentiles of windows using hourly histograms are approximate; request latency measurements are exact either way. Historical percentile figures below retain their original estimator. The 2026-11-03 correction uses continuous ranks and gives about 20.1% worst-case relative error for bounded interior buckets; first and final buckets have no such guarantee. It needs no migration or new datastore. See the [ADR amendment](decisions/0006-hourly-rollups-with-bucketed-histograms.md#2026-11-03-accuracy-amendment).
- For capacity beyond what one stack handles, see [Scale Spanlight](runbooks/scale.md).
