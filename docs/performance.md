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
- **Stack.** `deploy/compose.yaml` as shipped: one `api`, one `worker`, `web` and Postgres 17 (its stock settings plus `jit=off`; see [Tuning Postgres](../docs-site/src/content/docs/self-hosting.md) for the ones a larger host should change). The only change is an override file ([`backend/load/compose.load.yaml`](../backend/load/compose.load.yaml)) that publishes Postgres on `127.0.0.1` for the seeder, turns the demo traffic job off and, for the gateway scenario, adds a fake provider, allows the gateway to call it over plain `http://` (`GATEWAY_ALLOW_INSECURE_BASE_URLS`) and gives the api and worker a throwaway `CREDENTIALS_KEYS`. It changes no limit, pool size or code path.
- **Credentials.** Ingestion uses one API key with `ingest:write`. A key is limited to 50 requests a second, and the test sends 5. The read routes accept API keys with `traces:read`, and a key is limited to 20 reads a second (bursts of 40), which is the same as the target rate. The read scripts therefore spread their requests over ten keys, one per virtual user, so the limit never caps the test.
- **Overview.** The window rotates through 1 hour, 24 hours, 7 days and 30 days. The first two read the raw spans and give exact percentiles. The other two read the hourly rollups and say `"approximate": true`. Two checks in the script confirm that each response reports the source its window length calls for (`approximate`), and that the window has data, which for the 7-day and 30-day windows means the rollups hold rows. The p95 of each window is shown in the results but is not a threshold; the threshold is the p95 over all requests.
- **Between scripts.** Before each k6 script the workflow waits for the database to settle ([`settle.sh`](../backend/load/settle.sh)): it polls `pg_stat_activity` until no query of the application role is executing and no job is running, for at most 2 minutes, and carries on either way. k6 gives up on a slow request after its timeout, but the server keeps running the query, and the worker may still be rolling up hours, so without the pause one scenario would start on the leftovers of the one before.
- **Trace list.** Half of the requests are the first page the dashboard opens with (last 24 hours, no filter). The rest filter by one model, or to traces with errors.

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

## Known slower paths

These are real requests that the scripts do not send yet, so no threshold covers them.

- **Overview, time series or model table with an `environment` filter, for a window of 24 hours or less.** The environment belongs to the trace, so the query joins `traces` to the spans of the window and reads every trace of the project to do it. Without the filter the query reads the spans alone, from the index. In a test database of 2 million spans the filtered 24-hour overview took about four times as long as the unfiltered one, and the gap grows with the number of traces. Windows over 24 hours read the hourly rollups and are not affected. The overview script never sends `environment`, and there is no threshold for this path yet.

## Reading the numbers

- The seeded data is one project, so the figures say nothing about many busy projects at once.
- The seeder ends with `VACUUM ANALYZE`, so every page of the table is marked all-visible and the dashboard's index-only scans never visit the table. A live system gets there through autovacuum, and the migration that added the covering index also makes autovacuum visit `spans` after every 1 % of new rows, so recent pages are marked soon after they are written. A project that ingests faster than autovacuum keeps up will read the table for the newest rows.
- Ingestion is measured at a steady rate. A burst above 50 requests a second per key is limited by design and answered with `429` and `Retry-After`; see the [ingestion spike runbook](runbooks/ingestion-spike.md).
- Percentiles of the 7-day and 30-day windows are estimated from histograms and can be off by up to a quarter of the value; the latency of the request is exact either way.
- For capacity beyond what one stack handles, see [Scale Spanlight](runbooks/scale.md).
