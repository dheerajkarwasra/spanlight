# Doctor accuracy harness

Labelled scenarios for the 15 Doctor detectors (one positive and at least two negatives
each: a near miss just under a threshold and a legitimate pattern) plus three clean control
projects. One project per scenario, so fingerprints never collide. The result is a
precision / recall table per detector, the findings of other kinds that appeared anywhere
(cross-detector false positives), anything found in the controls, and whether the scenarios
with an expectation showed it.

Recall here is a harness number: it counts planted positives. Precision on real traffic comes
from user feedback (`spanlight doctor precision`); quote the two separately.

## Files

| File | What it holds |
|---|---|
| `scenarios.py` | the list (`scenario_types.py`: what a scenario is) |
| `traffic*.py` | what each scenario sends: shared helpers (`traffic.py`), error and Lab traffic, volume and shape traffic, synthetic spans, rate-limit shares, the provider-path scenarios, controls |
| `incident_feed.py` | the fixture status feed, its loopback server and transport |
| `common.py`, `clients.py`, `spans.py`, `context.py` | state and HTTP, ledger-guarded Anthropic calls, synthetic span builders, the run context |
| `runner.py`, `runner_setup.py`, `collect.py` | plan, setup, baseline, run, collect |
| `trigger.py` | enqueue the rollup and detector jobs, poll until they ran |
| `score.py` | the table (Wilson intervals from `app.insights.precision`) |
| `../setup.py`, `../ledger.py`, `../paths.py` | the pilot account, the spend cap, where state lives |

The offline tests are in `backend/tests/accuracy/` and run with the rest of the suite.

## State and secrets

Nothing the harness writes is tracked, and it reads no credential from the repository:

- `DOCTOR_HARNESS_STATE`: the pilot's state file (account, org, credential id, personal
  access token, generated password), written by `python -m accuracy.setup`. Default
  `.local/pilot/state.json` under the repository root (git-ignored); a relative path resolves
  against the repository root. The harness keeps its own projects, keys and run records in
  `doctor/state.json` beside that file, and the spend ledger in `ledger.json` beside it, so one
  variable moves all three. Every state file is written with mode 0600.
- `ANTHROPIC_API_KEY`: read from the environment at call time and sent only to Anthropic
  (directly) or to the local API, which seals it as the pilot credential. Nothing prints it.
- Results: `backend/accuracy/results/doctor_<ts>.jsonl` (git-ignored).
- `DOCTOR_HARNESS_PG_CONTAINER` (optional): the dev Postgres container the trigger runs `psql`
  in; default `idoc-test-pg`.
- `DR_SUFFIX` (optional): appended to every project name, for a run on fresh projects.

## Running

Everything runs from `backend/`, in the backend environment with the local SDK added:

```bash
cd backend
export ANTHROPIC_API_KEY=...            # from your shell or secret manager, never a file here
make doctor-accuracy                    # every step below, in order
```

or step by step:

```bash
RUN="uv run --with ../sdks/python python -m"
$RUN accuracy.doctor.runner --plan       # the table, estimated cost, ledger totals
$RUN accuracy.setup                      # pilot account, org, Anthropic credential, PAT (idempotent)
$RUN accuracy.doctor.runner --setup      # projects, ingest keys, routes, gateway keys, fault profiles
$RUN accuracy.doctor.runner --baseline   # model probe (2 tiny calls) + backdated baselines
$RUN accuracy.doctor.trigger --rollup-only
$RUN accuracy.doctor.runner --run        # live traffic; add --only error_spike,tool_loop:pos to narrow
$RUN accuracy.doctor.trigger             # rollup, detectors, poll until every project has a new run
$RUN accuracy.doctor.runner --collect    # -> accuracy/results/doctor_<ts>.jsonl
$RUN accuracy.doctor.score               # markdown table of the latest results
```

Offline tests (no network, no key, no stack): `cd backend && uv run pytest tests/accuracy -q`.

Prerequisites: the API on `:8000` and the worker running (`uv run python -m app.jobs.worker`),
the dev database in a local Postgres container (the trigger inserts jobs through
`docker exec … psql`; the container is `DOCTOR_HARNESS_PG_CONTAINER`, default `idoc-test-pg`), the price book synced (`uv run spanlight sync-prices`; `cost_spike`
needs priced spans), and `backend/.env` pointing at the dev database
(`provider_incident:with_incident` stores its fixture incident through the status poller).
Every paid call goes through the spend ledger (`ledger.json` beside the state file), a soft
spending cap: a call that could pass it is refused before it is made.

## Order of operations and timing

Detector windows: 15 min (`error_spike`, `retry_storm`, `rate_limit_pressure`), 30 min
(`provider_incident`), 1 h (`latency_regression`, `cost_spike`, `retry_after_ignored`,
`truncated_outputs`, `tool_loop`, `truncated_stream_accepted`, `unsupported_parameter_retried`),
24 h (`context_growth`, `cache_opportunity`, `client_timeout_misconfigured`, `unpriced_spend`).
Baselines read `span_rollups_hourly` over the 7 hour-aligned days ending at
`floor_hour(now - 24 h)` and need 100 calls per slice; `rollup_hourly` recomputes the last 48 h.

1. `--plan`: record the estimate. `accuracy.setup` and `--setup` are idempotent (reruns only
   create what is missing and correct what drifted).
2. `--baseline`: probes the two models, then posts 240 backdated spans per baseline scenario,
   dated 40 to 26 hours ago (inside the baseline period, inside the recompute window).
3. `trigger --rollup-only` and wait for it to report `done`. The baselines only exist for
   the detectors once this rollup has run. Do this within ~8 hours of step 2, or the oldest
   baseline spans fall out of the 48-hour recompute.
4. `--run`: scenarios run longest window first, so the 15-minute ones go last. The run prints
   when the first 15-minute scenario started and the deadline (start + 15 min) for step 5.
   Expect 10-15 minutes in all (the long tail: 320 slow latency calls, 40 x 50k-token prompts,
   one deliberate 61 s wait, a corrected client sleeping out three Retry-Afters).
5. `trigger` at once: enqueues `rollup_hourly` (so the hour's rollups include the run; not
   needed by the detectors but keeps the dashboards honest), then `run_detectors`, waits for the
   job, then polls `GET /projects/{id}/detector-runs` on every doctor project (both
   organizations' projects) until a run newer than the trigger appears. The scheduled
   `run_detectors` fires every 15 minutes on its own as well; a manual trigger is fine either
   way (fingerprints dedupe repeat findings).
6. `--collect`, then `score`. Keep the scored table with your run notes.

If step 5 cannot happen within 15 minutes of the first 15-minute scenario (a worker restart,
a slow run), rerun only those: `--run --only error_spike,retry_storm,rate_limit_pressure`,
then trigger. Reruns of a scenario add traffic to the same project; the 15-minute detectors
only see the last window, so a rerun replaces the evidence rather than piling it up. The
1-hour and 24-hour scenarios tolerate a late trigger as long as the detectors run within
their window of the run's end. A run that straddles a 15-minute boundary is harmless: the
detector window is `[now - 15 min, now)` at run time, not a wall-clock quarter hour, so what
matters is only that the whole scenario finished less than 15 minutes before the trigger.

`provider_incident` is organization-wide (30 min), so each of its scenarios lives in its own
organization (`Pilot-incident-<case>`) with two projects and one provider name per case; it
needs the trigger within 30 minutes of its spans.

## Provider-path scenarios

Three scenarios cover the provider-path behaviour added with the provider error fields:

- `rate_limit_pressure:spend_cap` (positive, synthetic): 60 calls, 12 of them Anthropic
  spend-cap 429s (`provider.error.type = rate_limit_error`, `provider.error.code =
  enforced_spend_limit_reached`, no retry-after). Expectation: the finding fires and every
  failed llm span of its evidence reads `rate_limit_kind = spend_cap` (collect reads it from
  the trace detail API).
- `provider_incident:with_incident` (positive, synthetic): the `pos` incident traffic (24
  anthropic 5xx of 30 calls over two projects), after a fixture incident is stored through the
  product's own status poller: `incident_feed.py` serves a one-incident Statuspage feed on a
  loopback server, and `poll_one` fetches it through `build_status_client` with only the
  transport swapped. Expectation: the finding's evidence carries `incident_id` and the seeded
  title. The fixture row is marked (title "Spanlight Doctor harness fixture…", external id
  `spanlight-doctor-<nonce>`) and stored already resolved; it stays in the local stack's
  `provider_incidents` until retention, and its link points at a page that does not exist.
- `rate_limit_pressure:gateway_limiter_negative` (negative, real): an 80-call burst through the
  gateway on a key with `rpm_limit = 50`. The key's bucket admits about 50 calls and the gateway
  answers the rest with its own `429 RATE_LIMITED`. Those refusals must never read as provider
  pressure, so the detector must stay quiet. The run fails if no call was refused (a burst that
  never reached the limit proves nothing).

## What is real and what is not

`--plan` shows the mode of every scenario. Real traffic (Python SDK or the gateway without a
fault profile) is used wherever a real call can produce the condition. Lab mode is real
gateway traffic under an Integration Lab fault profile; the faults the gateway answers itself
(429, 400, 5xx) cost nothing and the harness settles them at zero in the ledger. Synthetic
spans (`POST /v1/traces`) are used only for the baselines, `rate_limit_pressure` (fault 429s
are excluded by design and real 429s, spend caps included, are not reproducible on demand),
`unpriced_spend` (no real unknown model) and `provider_incident` (an organization-wide real
5xx storm cannot be produced; the status-page incident comes from a fixture feed).

Model names: the SDK stores the model the provider answers with, so `--baseline` first asks
each model once and records the answered name in state; every scenario then requests that
exact name so failed calls (no response) and successful ones share one `(environment, model)`
slice. Repeated or cacheable prompts carry a per-run nonce: prompt caches are shared across
projects on one Anthropic key.

## Results

`accuracy/results/doctor_<ts>.jsonl`, one row per scenario: `scenario`, `detector`, `label`,
`mode`, `projects`, `fired`, `fired_in`, `finding_kinds_in_project`, `severity`, `titles`,
`evidence_trace_ids`, `matched_own_traces`, `own_trace_count`, `run` (timing, calls,
failures, error), `run_facts`, `last_detector_run_at`, and for a scenario with an expectation
`expectation`, `expectation_met` and what was observed (`rate_limit_kinds` or
`incident_citations`). `score` prints TP/FN/FP/TN, precision and recall with Wilson 95 %
intervals per detector, the cross-detector findings, the control findings, the positives that
did not fire, and the expectation table.
