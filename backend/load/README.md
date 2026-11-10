# Load tests

[k6](https://k6.io) scripts and a seeder that measure the shipped Compose stack. What is measured, the thresholds and the method are in [Performance](../../docs/performance.md); this page is about running them.

| File | What it does |
|---|---|
| `seed.py` | Creates an organization, a project, one ingest key and ten read keys, then stores spans through the real ingestion pipeline, oldest first, builds the hourly rollups, runs the aggregate repair until no seeded hour is pending (a pending hour is read from the spans, which is not what the scenarios should measure) and runs `VACUUM ANALYZE`. Writes the keys to a JSON file. |
| `seed_data.py`, `seed_workspace.py` | Used by `seed.py` and kept beside it: the generator of the spans (no I/O; each batch holds one slice of the 28 days, and about 2 % of the traces arrive up to an hour late), and the creation of the organization, owner, project and keys. |
| `seed_alerts.py`, `seed_alert_rules.py` | The alert evaluation test: 50 projects with 20 alert evaluations and 7 days of spans each. See [alerts.md](alerts.md). |
| `settle.sh` | Waits (at most 2 minutes) until no application query and no job is running. The workflow calls it before each k6 script. |
| `ingest.js` | 500 spans a second (5 requests of 100 spans) to `POST /v1/traces`. Threshold: p95 below 200 ms. |
| `overview.js` | 20 requests a second to the overview metrics, rotating through 1 hour, 24 hours, 7 days and 30 days. Threshold: p95 below 300 ms. |
| `traces.js` | 20 requests a second to the trace list: the first page, one model, failed traces only, and the first page sorted by duration, cost, tokens or retry cost; the summary gives each variant's median and p95. Threshold: p95 below 300 ms. |
| `mixed.js` | Reads during ingestion: `INGEST_RPS` requests a second of 100 spans (default 5; 0 leaves ingestion out) while the overview and the trace list are each read `READ_RPS` times a second (default 10), all in parallel, with the requests of `ingest.js`, `overview.js` and `traces.js`. Each scenario has its own thresholds: ingest p95 below 200 ms, reads p95 below 300 ms. Writes `<NAME>.json` and `<NAME>.md` (default `mixed`). |
| `gateway.js` | The LLM gateway's overhead. Two scenarios of 50 requests a second for 60 s, one after the other: a chat completion through `/gw/v1/chat/completions`, then the same request straight to the fake provider. Thresholds: p95 through the gateway below 25 ms, p95 direct below 5 ms; the overhead (the difference of the two p95 values) must be below 20 ms. `GATEWAY_SCENARIOS=gateway,budget` measures budget admission instead (see step 4). |
| `fake_provider.py` | A small ASGI app that answers `POST /v1/chat/completions` and `GET /v1/models` with fixed bodies after `FAKE_PROVIDER_DELAY_MS` (default 0). Run by `compose.load.yaml`. |
| `seed_gateway.py` | Creates a workspace, an `openai_compatible` credential on the fake provider, a route and a gateway key without limits, and writes the key to a JSON file. Needs no spans. With `--budget-spans N` it also adds a second key, N priced spans of that key this UTC month and a monthly `block` budget of $1,000,000 on it, raises the project's retention to 31 days (a monthly budget needs it), and writes that key as `budget_key`. |
| `seed_tour.py` | Runs after `seed.py` on its project: password users (owners of its organization), a `scores:write` key, three datasets of 200 items copied from seeded traces, two prompts of three versions, a heuristic evaluator, an annotation queue of 100 traces, an experiment with two finished runs (inserted, not executed) and one trace of 5 000 spans. Writes the passwords, the key and the ids to a second JSON file. |
| `tour.js` | The product tour: sixteen scenarios in parallel, reads with a session cookie (trace, 5 000-span trace, span and cost explanation, sessions, users, attribution, insights, datasets, experiments and compare, evaluators, prompts, annotation queue), OTLP ingest in JSON and protobuf, `POST /v1/scores` and a slow sign-in scenario. Threshold: p95 below 500 ms for every read endpoint. |
| `summary.js` | Used by the scripts: turns the k6 results into the Markdown table and the JSON summary. |
| `compose.load.yaml` | The only change to `deploy/compose.yaml`: publishes Postgres on `127.0.0.1` for the seeder, turns the demo traffic job off and, for the gateway test, adds the fake provider (published on `127.0.0.1:9000`), sets `GATEWAY_ALLOW_INSECURE_BASE_URLS` and gives the api and worker a throwaway `CREDENTIALS_KEYS`. |

Run `settle.sh` between two scripts on your machine too, so the second does not start on the leftovers of the first: `LOAD_ENV_FILE=deploy/.env COMPOSE_PROJECT_NAME=spanlight-load COMPOSE_FILE=deploy/compose.yaml:backend/load/compose.load.yaml backend/load/settle.sh`.

Every script also has three more thresholds: `http_req_failed` below 1 % (a `429` is a failure), `checks` above 99 %, and `dropped_iterations` equal to 0, which means the target rate was really held (k6 drops an iteration when it has no free virtual user left). A script exits non-zero when any threshold is missed.

## On GitHub

Open the repository's Actions tab, choose **Load test** and **Run workflow**.

- `realistic` (the default) seeds 1 million spans, about 33 000 LLM calls a day for 30 days, which is a small production team. Each scenario runs for 1 minute. The job has a limit of 90 minutes.
- `smoke` seeds 100 000 spans and runs each scenario for 30 seconds. It checks that the setup works and has a limit of 30 minutes.
- `full` is the stress run: it seeds 10 million spans and runs each scenario for 2 minutes (ingestion 3 minutes). It takes several hours, almost all of it seeding.

After the three scenarios on the seeded data, every profile runs `mixed.js` in three stages, `mixed-baseline` (no ingestion), `mixed-half` (5 ingest requests a second) and `mixed-near-knee` (10), then the gateway overhead script for 60 seconds a scenario. A missed threshold in `mixed-near-knee` is reported in its table but does not fail the job. All three use the same thresholds and run on a GitHub-hosted `ubuntu-latest` runner. The run's summary page shows the run's environment and one table per script; the raw k6 summaries are attached as the artifact `load-results-<profile>`. The job is red when any threshold is missed or the gateway overhead is not under 20 ms, and all the scripts run either way.

## On your machine

You need Docker with the Compose plugin, [uv](https://docs.astral.sh/uv/) and, to run the scripts without Docker, [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/). Run everything from the repository root.

1. Start a stack of its own. The project name keeps it apart from a development stack, and `deploy/.env` needs `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `SECRET_KEY` (see `deploy/.env.example`).

   ```bash
   docker compose -p spanlight-load -f deploy/compose.yaml -f backend/load/compose.load.yaml \
     --env-file deploy/.env up -d --build
   curl -s http://localhost:8080/health/ready
   ```

2. Seed it. The seeder connects as the database owner, so it needs the `postgres` password. It reads the URL from `LOAD_DATABASE_URL` and not from `DATABASE_URL`, so a shell that points at a development database cannot fill it by accident.

   ```bash
   cd backend
   export LOAD_DATABASE_URL="postgresql+psycopg://postgres:$(sed -n 's/^POSTGRES_PASSWORD=//p' ../deploy/.env)@127.0.0.1:55433/spanlight"
   uv run python load/seed.py --spans 100000 --credentials-file ../.local/load/credentials.json
   cd ..
   ```

   `--spans` is how many spans to store (use at least 100 000: with fewer, a window or a filter of the scripts can have nothing to return) and `--seed` repeats a data shape. One seeder process runs at about one CPU core; `--processes N` stores the batches in N processes, each taking every N-th batch, which is how a seed of 100 million spans fits in hours. The seeder prints its rate and the time left with every million spans, and stops early when the seed is projected to take longer than `--max-minutes` (default 240). The workflow passes 15 for `smoke`, 45 for `realistic` and 240 for `full`, which is what is left of the job's limit once the build, the rollups and the scenarios are counted.

   The keys are in the credentials file (mode 600, in the git-ignored `.local/`) and are never printed. It also holds `data_end`, the instant the seeded data ends at. The overview and trace list scripts ask for windows that end there, not at the wall clock, so a seed that takes hours does not leave the newest windows empty.

3. Run a script. The paths are the ones the scripts read, so give them as absolute paths. The gateway script is the exception, see below.

   ```bash
   mkdir -p /tmp/spanlight-load-results
   k6 run \
     -e CREDENTIALS_FILE="$PWD/.local/load/credentials.json" \
     -e RESULTS_DIR=/tmp/spanlight-load-results \
     -e DURATION=30s \
     backend/load/overview.js
   ```

   Or with the image the workflow uses (host networking, so on Linux, or in Docker Desktop with host networking turned on):

   ```bash
   docker run --rm --network host --user "$(id -u):$(id -g)" \
     -e K6_NO_USAGE_REPORT=true -e DURATION=30s \
     -v "$PWD/backend/load:/load:ro" -v "$PWD/.local/load:/creds:ro" \
     -v /tmp/spanlight-load-results:/results \
     grafana/k6:2.3.0 run /load/overview.js
   ```

   Each script writes `<name>.json` (the full k6 summary) and `<name>.md` (the table) to the results directory.

4. To run the gateway overhead test, seed the gateway (it can follow `seed.py` or stand alone) and run `gateway.js`. The fake provider is part of the stack from step 1. `seed_gateway.py` seals the fake provider's key with `LOAD_CREDENTIALS_KEYS`; leave it unset to use the throwaway keyring that `compose.load.yaml` also defaults to, and if you set it, set it for both the stack and the seeder.

   ```bash
   cd backend
   uv run python load/seed_gateway.py --credentials-file ../.local/load/gateway-credentials.json
   cd ..
   k6 run \
     -e GATEWAY_CREDENTIALS_FILE="$PWD/.local/load/gateway-credentials.json" \
     -e RESULTS_DIR=/tmp/spanlight-load-results \
     backend/load/gateway.js
   ```

   To measure budget admission (the history read every call under a `block` budget makes, ADR 0017), seed with `--budget-spans 1000000` and run `-e GATEWAY_SCENARIOS=gateway,budget`: the `budget` scenario sends the same request at the same rate with the budget key, and `gateway.json` holds `budget_overhead` (the p95 of `budget` minus the p95 of `gateway` over successful calls, which should be under 5 ms) with `ok`, which is false when any `budget` call failed. Add `-e GATEWAY_VUS=32` for a concurrency run with a slow provider (`FAKE_PROVIDER_DELAY_MS=1000` on the stack); the stack's gateway admission limits (`GATEWAY_MAX_IN_FLIGHT`, `GATEWAY_MAX_IN_FLIGHT_BYTES` and `GATEWAY_MAX_IN_FLIGHT_PER_KEY`) cap the calls in flight. The results are in [Performance](../../docs/performance.md#budget-admission).

   The sustained run checks that admission does not slow down as the open hour fills: start it within a minute after a UTC hour boundary, `k6 run -e GATEWAY_SCENARIOS=budget -e GATEWAY_DURATION_SECONDS=420 -e GATEWAY_CREDENTIALS_FILE=<creds> backend/load/gateway.js`, so about 21 000 settled calls land in one hour; `budget_overhead_ms` is then the run's `budget` p95 minus the `gateway` p95 of the stack's earlier `gateway,budget` run, and must stay under 5 ms.

   To measure gateway admission, start the stack with `FAKE_PROVIDER_DELAY_MS=1000` and run `-e GATEWAY_SCENARIOS=concurrency`: 200 virtual users (`GATEWAY_VUS` overrides the number) send streaming chat completions in a loop, alternating over the two keys `seed_gateway.py` writes, and the fake provider streams three chunks and `[DONE]` after the delay. Any refused or failed call fails the run; the latency counts successful calls only, and `gateway.json` holds `concurrency` (`success_p95_ms`, `refused`, `ok`). The results are in [Performance](../../docs/performance.md#gateway-admission).

   `LOAD_DATABASE_URL` is the one from step 2. The script writes `gateway.json` and `gateway.md`. Unlike the other scripts it does not use `DURATION`: each scenario lasts `GATEWAY_DURATION_SECONDS` (default 60). k6 cannot fail a run on the difference of two thresholds, so the script puts `overhead` with `ok` in `gateway.json`; the workflow checks it with `jq -e '.overhead.ok == true'`, and so can you.

5. To measure reads during ingestion, run `mixed.js` once per stage, with `settle.sh` before each, on the stack and credentials from steps 1 and 2:

   ```bash
   for stage in baseline:0 half:5 near-knee:10; do
     LOAD_ENV_FILE=deploy/.env COMPOSE_PROJECT_NAME=spanlight-load \
       COMPOSE_FILE=deploy/compose.yaml:backend/load/compose.load.yaml backend/load/settle.sh
     k6 run \
       -e CREDENTIALS_FILE="$PWD/.local/load/credentials.json" \
       -e RESULTS_DIR=/tmp/spanlight-load-results \
       -e DURATION=30s -e INGEST_RPS="${stage#*:}" -e NAME="mixed-${stage%%:*}" \
       backend/load/mixed.js
   done
   ```

   Each stage writes `mixed-<stage>.json` and `mixed-<stage>.md`: the thresholds per scenario, then the overview p95 per window and the trace list p95 per variant. k6 exits non-zero for any stage that misses a threshold; the workflow ignores that only for `near-knee`.

6. To tour the rest of the product, seed it on the project from step 2 and run `tour.js`. `TOUR_RPS` multiplies every rate (default 1, about 50 requests a second in all). The sign-in scenario runs `LOGIN_PER_MINUTE` sign-ins a minute (default 3) and refuses more than one per seeded user a minute, because the api hashes at most 20 passwords per email and address in 15 minutes; `--login-users` seeds more users. `ORIGIN` must be an origin the api allows (default `BASE_URL`).

   ```bash
   cd backend
   uv run python load/seed_tour.py --credentials-file ../.local/load/credentials.json \
     --tour-file ../.local/load/tour-credentials.json
   cd ..
   docker run --rm --network host --user "$(id -u):$(id -g)" \
     -e K6_NO_USAGE_REPORT=true -e DURATION=5m -e TOUR_RPS=1 \
     -v "$PWD/backend/load:/load:ro" -v "$PWD/.local/load:/creds:ro" \
     -v /tmp/spanlight-load-results:/results \
     grafana/k6:2.3.0 run /load/tour.js
   ```

   It writes `tour.json` and `tour.md`: the thresholds per scenario, then the median, p95, p99 and max of every endpoint.

7. Remove the stack and its volumes when you are done:

   ```bash
   docker compose -p spanlight-load -f deploy/compose.yaml -f backend/load/compose.load.yaml \
     --env-file deploy/.env down --volumes
   ```

## Settings of the scripts

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | `http://localhost:8080` | The `web` service. The scripts go through Caddy, the path a real request takes. |
| `DURATION` | `30s` | How long the arrival rate is held. |
| `CREDENTIALS_FILE` | `/creds/credentials.json` | The file `seed.py` wrote. |
| `RESULTS_DIR` | `/results` | Where the JSON and Markdown summaries are written. It must exist. |
| `INGEST_RPS` | `5` | `mixed.js` only: ingest requests a second, 100 spans each. `0` runs the reads alone. |
| `READ_RPS` | `10` | `mixed.js` only: requests a second of each of its two read scenarios. |
| `NAME` | `mixed` | `mixed.js` only: the name of its result files. |
| `FILTER_MODEL` | `claude-haiku-4-5` | `traces.js` and `mixed.js`: the model of the filtered requests. It is the most common seeded one. |
| `GATEWAY_CREDENTIALS_FILE` | `/creds/gateway-credentials.json` | `gateway.js` only: the file `seed_gateway.py` wrote. |
| `FAKE_PROVIDER_URL` | `http://localhost:9000` | `gateway.js` only: where the fake provider is published, for the direct scenario. |
| `GATEWAY_DURATION_SECONDS` | `60` | `gateway.js` only: how long each of its scenarios holds the rate. |
| `GATEWAY_SCENARIOS` | `gateway,direct` | `gateway.js` only: the scenarios to run, one after the other: `gateway`, `direct` and `budget` (needs `--budget-spans`). |
| `GATEWAY_VUS` | unset | `gateway.js` only: when set, a positive integer; each scenario runs this many virtual users in a loop instead of 50 requests a second. Any other value stops the run. |
| `FAKE_PROVIDER_DELAY_MS` | `0` | A setting of the stack, not of k6: how long the fake provider waits before it answers. Set it before `docker compose up`. |

## Reading a failed run

- **A script wrote no `.json` or `.md`.** When `handleSummary` throws, k6 prints its default summary and still exits 0, so its exit status does not show it. Look for a `handleSummary` or `summarize` error in the script's log. The workflow checks that every script wrote both files and fails the job, with an error naming the missing file, when one did not.
- **Many `429` responses.** The read scripts use ten keys so no key gets near its limit of 20 requests a second. If you changed the rate or the number of keys, check that each key still stays below it.
- **`gateway.js` stops in its setup with a gateway error.** The warm-up call failed: the answer's first 300 characters are in the message. `401` means the key is not the one `seed_gateway.py` wrote to the file you mounted; a `502`-style error usually means the api cannot reach `fake-provider:9000` or cannot open the sealed key (the stack and the seeder used different keyrings).
- **The gateway overhead is over the limit but both thresholds pass.** The p95 of the gateway scenario is below 25 ms while the direct one is a few milliseconds, so the difference is over 20 ms. The table shows both figures.
- **Dropped iterations.** k6 had no free virtual user, so it did not start requests at the target rate. The server was too slow to keep up, and the latency figures understate it.
- **The check "the window has traces" fails.** The overview of a window came back empty. The seeded data covers all four windows, so for 7 and 30 days this is what missing rollups look like (the seeder stops when its backfill writes no rows): run `spanlight rollups backfill` for the project, or seed again. For 1 and 24 hours it means the data is older than the windows, or too few spans were seeded.
- **The check "reported the expected source" fails.** The API said `"approximate"` the wrong way round for a window. It is true when the window or the previous period of equal length reads a whole hour from the rollups ([API deviations](../../docs/api-deviations.md), "Metrics source"): never for 1 hour (unless `data_end` falls on the hour, when either answer passes), always from 24 hours up, because the raw part never reaches back more than 24 hours. A `true` for 1 hour also comes back when the rollups are unavailable and the whole window is read from them; otherwise the API's rule changed.
- **The check "the page has traces" fails.** The data does not match the filter, usually because too few spans were seeded.
