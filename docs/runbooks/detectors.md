# Run the Doctor: detector runs, stuck projects, tuning, explanation budget

## Purpose and when to use

The Doctor's detectors run in the worker every 15 minutes. For each project with a span in the last 24 hours they read the recent spans, apply 15 fixed rules, and open, update or resolve insights (what an insight is and how it is opened, muted and resolved is in [the Doctor guide](../../docs-site/src/content/docs/doctor.mdx); what each detector looks for is in [the detector catalogue](../../docs-site/src/content/docs/reference/detectors.md)). Use this page when:

- no insights appear and you expect some, or the Doctor looks late;
- a detector shows errors in `GET /detector-runs`, or `spanlight_detector_runs_total{outcome="error"}` grows;
- one project's runs time out, so none of its detectors report;
- an insight is too noisy and you want to mute it, or the whole feature must be switched off;
- "Explain with Claude" is refused, or the monthly explanation budget needs a look;
- the provider status feeds show a stale badge, or `spanlight_provider_status_polls_total{outcome="parse_error"}` rises.

Detectors are deterministic code over your stored spans. They call no model, send nothing anywhere, and write only to `insights` and `detector_runs` (plus notification rows for a critical insight that opens). Switching them off loses nothing already found.

## Prerequisites

- Shell access to the host and the database owner (`postgres` in the Compose stack) for the SQL below.
- For the API calls: a personal access token with the `read` scope for a member of the project. Detector runs are not readable with a project API key.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- `$BASE` is the public address of the dashboard, as in the [overview](README.md#conventions-used-in-these-pages).

## Is the job running?

The scheduler enqueues one `run_detectors` job every 15 minutes (dedupe key `run_detectors:<period start>`); the worker runs it. The job has a single attempt: the next period is the retry.

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT id, status, outcome, attempts, now() - created_at AS age, left(last_error, 100) AS last_error
     FROM jobs WHERE kind = 'run_detectors' ORDER BY id DESC LIMIT 5"
```

Healthy: a `done` row about every 15 minutes. Rows that stay `queued` and grow older mean the worker is down or busy: see [Is the worker alive?](README.md#is-the-worker-alive). No rows at all means `DETECTORS_ENABLED` is off (see [Disable the detectors](#disable-the-detectors)).

The worker logs one line per pass:

```bash
spl logs --since 20m worker | grep -E 'detectors_ran|detectors_out_of_time|detector_project_failed|detector_project_timed_out|detector_failed'
```

`detectors_ran` carries `projects`, `projects_failed`, `projects_gone`, `projects_left`, `runs`, `errors`, `opened` and `notified`. `projects_left` above 0 (and `detectors_out_of_time`) means the pass hit its time budget; see [A project that times out](#a-project-that-times-out).

## Read the detector runs

Every detector writes one row to `detector_runs` per project per pass, whether or not it found anything. This is the Doctor's own health record.

With the API (newest first, at most 100):

```bash
read -rs TOKEN; echo
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/projects/<project-id>/detector-runs?limit=30"
```

Each item has `detector`, `window_start`, `window_end`, `findings`, `truncated`, `duration_ms`, `error` and `ran_at`. With SQL, across all projects, as the database owner:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT detector, count(*) AS runs, count(*) FILTER (WHERE error IS NOT NULL) AS errors,
          max(ran_at) AS last_run, round(avg(duration_ms)) AS avg_ms
     FROM detector_runs WHERE ran_at > now() - interval '1 hour'
    GROUP BY detector ORDER BY errors DESC, detector"
```

How to read a row:

| Field | Meaning |
| --- | --- |
| `findings` | How many findings the detector returned, `0` for a clean run. `null` when the run failed. |
| `error` | `null` on success; otherwise the exception class and the first line of its message, at most 500 characters. A failed detector opens nothing and resolves nothing in that pass, so its insights keep their state. |
| `truncated` | The project had more than 50 000 LLM spans (or tool spans) in 24 hours, so the detectors saw only the newest 50 000. Short-window detectors are unaffected; the 24-hour ones (`context_growth`, `cache_opportunity`, `client_timeout_misconfigured`, `unpriced_spend`) may miss older calls. |
| `duration_ms` | The detector's own time over the loaded data. Every detector should take milliseconds; seconds means a very large project. |
| `ran_at` | When the row was written. A project with no recent `ran_at` has not been reached: see below. |

Rows older than 7 days are deleted by the nightly retention job.

**An error on one detector** (for example `ValueError: …`) is a bug in that detector for that data, not a problem with your system. The other detectors are unaffected. The log line `detector_failed` names the project, the detector and the exception type with its traceback (never span contents). Report it with that line.

**An error on every detector of a project** starting with `TimeoutError` or a database error is a failure around the detectors, not in them: see the next section.

## A project that times out

A pass stops starting new projects after 40 seconds, so it ends inside the worker's 50-second task limit, and each project may take at most 30 seconds (never past 45 seconds into the pass). The projects whose detectors ran longest ago go first, so a project the pass did not reach leads the next pass.

A project that fails or runs out of time gets an error row for each detector that had not already recorded one, for example `TimeoutError: the project's run took longer than 30 s`. Because that row has a fresh `ran_at`, the project moves to the back of the order, and the other projects are not held up by it. Its insights keep their state and nothing new is found for it until it succeeds.

1. Find the projects that are failing:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT project_id, count(*) AS rows, max(ran_at) AS last_error_at, max(error) AS error
        FROM detector_runs
       WHERE ran_at > now() - interval '6 hours' AND error LIKE 'TimeoutError%'
       GROUP BY project_id ORDER BY rows DESC"
   ```

2. Find the cause. The slow step is almost always reading the project's last 24 hours of spans, which is capped at 50 000 spans per kind:

   - Is the project very large? Check its volume: `SELECT count(*) FROM spans WHERE project_id = '<project-id>' AND started_at > now() - interval '24 hours'`.
   - Is the database busy or short of memory or connections? See [Scale Spanlight](scale.md). The detectors share the database with ingestion and the dashboard.
   - Are the detector indexes there? Migrations `0400`, `0401` and `0405` build indexes on `spans` and `traces`. Check that `spl logs migrate` ended cleanly, and look for an invalid index left by an interrupted build: `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid`. Drop it and restart `migrate` (`spl up -d`) so the migration builds it again.
   - Read the worker log for the project: `spl logs --since 1h worker | grep <project-id>`. `detector_project_timed_out` and `detector_project_failed` carry the project id and, for a failure, the error type.

3. Relieve it. Add database capacity, shorten the project's retention if its history is the problem, or turn the feature off while you fix it (see [Disable the detectors](#disable-the-detectors)). There is no per-project switch and no tunable cap: the limits are fixed so that every operator sees the same behaviour.

4. Verify: the next pass writes `ok` rows for the project (`error IS NULL`), `spanlight_detector_projects_skipped_total` stops growing, and the `detectors_ran` line shows `projects_failed=0`.

## Metrics and the Grafana row

The worker exports:

| Metric | Meaning |
| --- | --- |
| `spanlight_detector_runs_total{detector,outcome}` | Detector runs by outcome, `ok` or `error`. |
| `spanlight_detector_duration_seconds{detector}` | How long a detector took over one project (histogram). |
| `spanlight_insights_open{severity}` | Open insights across every project, refreshed at the end of each pass. |
| `spanlight_detector_projects_skipped_total` | Projects a pass did not start because its time budget ran out. |

The bundled Grafana dashboard (`deploy/grafana/spanlight-dashboard.json`) has a **Doctor** row with four panels: *Detector runs by outcome*, *Detector duration (p95)*, *Open insights by severity* and *Projects skipped by the run budget*. Useful queries when you build your own alerts:

```promql
# Any detector failing in the last hour
sum by (detector) (increase(spanlight_detector_runs_total{outcome="error"}[1h])) > 0

# Passes that cannot reach every project (sustained for an hour)
sum(increase(spanlight_detector_projects_skipped_total[1h])) > 0

# A detector slower than a second at p95
histogram_quantile(0.95, sum by (le, detector) (rate(spanlight_detector_duration_seconds_bucket[1h]))) > 1
```

`spanlight_insights_open` counts insights in status `open` only. The dashboard's navigation badge and the health score also count `acknowledged` ones.

## Tuning: muting

Detector thresholds are constants in the code, not settings, so every installation sees the same findings for the same traffic. To quiet an insight that is true but not worth acting on:

- **Mute the insight** (Doctor, the insight, **Mute**): pick a date up to 90 days ahead and give a reason. A muted insight keeps counting occurrences but never notifies. When the mute ends it reopens if the problem is still there and resolves if it is gone. An admin can **Unmute** at any time. With the API, `POST /api/v1/projects/<project-id>/insights/<insight-id>/mute` with `{"until": "<future time>", "reason": "<1 to 500 characters>"}` (an admin or owner token with the `write` scope); a mute date in the past or more than 90 days away is `422 INVALID_MUTE`.
- **Resolve it** if you have fixed the cause. It reopens only if the problem is seen again.
- **Stop notifications for a project** by removing channels from **Settings, Project, Insight notifications**. Insights are still found and shown.

A mute applies to one insight, which is one kind for one key (for example `error_spike` for one environment and model). There is no way to turn off one detector for a project. If a whole kind is wrong for you, mute its insights, or tell us why the rule is wrong.

If a detector keeps firing where you believe it should not (the evidence traces do not show the problem), keep the example traces and the insight's evidence and report it: the rule, not your data, may need to change.

## Disable the detectors

Use this while you restore a database, migrate data, or when the detectors overload the database and you cannot add capacity.

1. In `deploy/.env` set `DETECTORS_ENABLED=false`, then recreate the worker:

   ```bash
   spl up -d worker
   ```

   On Railway set the variable on the `worker` service and redeploy it.

2. Verify: after 30 minutes no new `run_detectors` row appears in the `jobs` table.

While the detectors are off nothing is opened or resolved, so open insights stay open and no automatic resolution happens. Notifications already queued are still delivered. Insights, their history and the Doctor screen remain readable. The health score keeps working: it reads the open counts and the metrics, not the detectors. To turn them back on, remove the line (or set `true`) and recreate the worker. The first pass resolves what has not been seen for 24 hours and opens what is happening now.

`USER_STATS_ENABLED=false` is the separate switch for the Users page's refresh job; see [Configuration](../../docs-site/src/content/docs/configuration.md).

## Provider status feeds

The worker polls the status pages of Anthropic, OpenAI and Google every 5 minutes (job `poll_provider_status`, one attempt, deduplicated per period like the detectors), so the Provider health page and the `provider_incident` insight can cite official incidents. The feeds are fixed in `backend/app/provider_status/adapters/`. Each feed is polled on its own: one failing never stops the others, and incidents already stored are kept whatever happens.

Every request is counted in `spanlight_provider_status_polls_total{provider,outcome}` and logged as one `provider_status_polled` line per provider and run, with the outcome and counts, never the feed body:

| Outcome | Meaning | What to do |
| --- | --- | --- |
| `fetched` | The feed answered `200` and was parsed. | Nothing. |
| `not_modified` | The feed answered `304`: nothing new since the last poll. Counts as a success. | Nothing. |
| `http_error` | Any other status, a redirect, or a transport error. | Check that the worker can reach the host on port 443. Usually the feed recovers by itself. |
| `timeout` | No answer within 10 seconds. | As `http_error`. |
| `too_large` | The body was over 2 MB. | The feed grew or changed; treat it as `parse_error`. |
| `parse_error` | The body is not the JSON or RSS the adapter expects. | The provider changed the feed format. See below. |
| `blocked` | The host resolved to a private or local address, so the egress check refused it. Logged at error. | A DNS or network configuration problem on your side. |

For OpenAI, a failed JSON feed is followed by its RSS feed in the same run, and both requests are counted.

**Is the job running?** Read the poll state of each feed:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT provider, last_polled_at, last_success_at, last_outcome, consecutive_failures, last_error_type
     FROM provider_status_sources ORDER BY provider"
```

`last_polled_at` older than 10 minutes means the worker is not polling: check that it runs (see [Is the worker alive?](README.md#is-the-worker-alive)) and that `PROVIDER_STATUS_POLLING_ENABLED` is not `false`. The Provider health page shows a stale badge when a feed has not been fetched for 30 minutes or its last poll failed. `GET /api/v1/projects/<project-id>/provider-incidents` returns the same poll state in `sources`.

**`parse_error` keeps firing.** The provider changed its feed format, and the adapter no longer reads it.

```promql
sum by (provider) (increase(spanlight_provider_status_polls_total{outcome="parse_error"}[1h])) > 0
```

1. Fetch the live feed as the fixtures were fetched; the commands are in [the fixtures README](../../backend/tests/provider_status/fixtures/README.md).
2. Refresh that provider's recorded fixture with the new response, and run `uv run pytest tests/provider_status` in `backend/`. The adapter tests now fail the way the live feed does.
3. Fix the adapter in `backend/app/provider_status/adapters/<provider>.py` until the tests pass, and deploy. The next poll writes the incidents the feed still lists; incidents that dropped out of the feed meanwhile are not recovered.

**Air-gapped servers.** Set `PROVIDER_STATUS_POLLING_ENABLED=false` on the api and the worker and recreate both. Nothing is fetched, a run queued before the change is skipped, and the Provider health page says the feeds are off on this server.

Incidents are kept for 730 days, then deleted by the retention job.

## Explanation budget

"Explain with Claude" is paid for by the organization's own Anthropic credential and capped per UTC month by `EXPLAIN_MONTHLY_BUDGET_USD` (default `1.00`). Explanations are opt-in, admin-only, and sent only on a click; see the guide for what is sent.

| Answer | Meaning and fix |
| --- | --- |
| `402 EXPLAIN_BUDGET_EXCEEDED` | The month's explanation cost plus this request's worst case (reservations for requests in flight included) is over the budget. It resets on the 1st, UTC. To allow more, raise `EXPLAIN_MONTHLY_BUDGET_USD` on the api and the worker and restart them. |
| `409 NOT_CONFIGURED` | The organization has no Anthropic credential (add one under Gateway, Credentials), `CREDENTIALS_KEYS` is not set, or the budget is `0`, which turns explanations off. |
| `409 EXPLAIN_MODEL_UNPRICED` | `EXPLAIN_MODEL` has no price, so the budget cannot be enforced. Use a priced model or add a price override. |
| `502 EXPLAIN_FAILED` | The call to Anthropic failed. Usually nothing is counted: when the gateway never sent the request, or Anthropic answered an error before generating, the reservation is removed. When the provider may have billed the call (a timeout or a lost connection after the request was sent), its worst-case cost stays in the month's spend. Check the credential with **Check** on Gateway, Credentials, and the gateway log. |

How much has been spent this month, by organization:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT p.org_id, count(*) AS explanations, sum(e.cost_usd) AS spent_usd
     FROM insight_explanations e JOIN projects p ON p.id = e.project_id
    WHERE e.created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    GROUP BY p.org_id ORDER BY spent_usd DESC"
```

A row with `completed_at` null is a **reservation**: an explanation in flight, or one whose request died. It counts against the budget at its worst-case cost. The cleanup job completes reservations older than 10 minutes at that cost (`completed_at` set, `text` null) instead of deleting them, because the provider may have billed a call whose outcome was never recorded; the spend is not refunded. Rows with `completed_at IS NULL` and `created_at` older than 10 minutes mean the cleanup is not running (see [Is the worker alive?](README.md#is-the-worker-alive)).

Each explanation is also a normal LLM span in the project, in the environment `doctor` with the tag `doctor-explain`, and the audit log records `insight.explain` with the insight, the model and the cost. Use those to see who asked and what it cost.

Back out: nothing to undo; raising or lowering the budget is a configuration change, and spent amounts are not refunded or reset.

## Related

- [The detector catalogue](../../docs-site/src/content/docs/reference/detectors.md): what each detector reads and the fix and check it suggests.
- [The Doctor guide](../../docs-site/src/content/docs/doctor.mdx): how insights are opened, muted and resolved, and the health score.
- [Alerts runbook](alerts.md): a critical insight reaches its channels through the same outbox, so a stuck outbox delays insight notifications too.
- [Operator runbooks overview](README.md): the first five minutes of any incident, and the worker check.
- [SLO runbook](slo.md): what healthy looks like and the alerts on the worker.
