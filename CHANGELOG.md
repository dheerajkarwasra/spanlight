# Changelog

All notable changes to Spanlight are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project is pre-1.0, so minor versions may change behaviour; breaking changes are called out.

## [0.6.0] - Unreleased

Prompts, datasets and evals: manage prompts as versions, score traces by hand or automatically, and compare a change on your own test cases before it ships.

### Added

- **Prompt registry.** Named prompts, `text` or `chat`, with immutable numbered versions and movable labels such as `production`. Templates use `{{variable}}` and are checked identically by the server, the dashboard and the SDK. A prompt can get 100 versions a day, saving an identical version is refused, and a prompt that an evaluator or experiment uses cannot be deleted. Each version shows its use over the last 7 days (traces, LLM calls, cost, p95 duration).
- **Prompts for your application.** `GET /v1/prompts/{name}?label=` or `?version=` with an API key that has the new `prompts:read` scope, answered with an `ETag` so an unchanged prompt is a `304`. Spans can carry the id of the prompt version they used (`prompt_version_id`), Traces can be filtered by it, and trace detail links to it.
- **Playground.** Run a prompt version or typed messages on up to three credential and model configurations side by side. Every run is a real provider call, recorded as a trace in the `playground` environment; the playground has no spend cap of its own beyond the project's blocking budgets, and is limited to 30 runs a minute per person.
- **Scores.** Numeric, boolean and categorical scores on a trace or a span, from the API (`POST /v1/scores`, scope `scores:write`, up to 1000 per request with per-score rejection), by hand on the trace page, or from evaluators. Score configs (type, range, categories) are optional: the first score with a name creates an implicit one. Traces can be filtered by score (`score_name` with `score_min`, `score_max` or `score_value`, and `has_scores`), and the trace list shows each trace's latest scores.
- **Annotation queues.** Queue traces for review under Review. Reviewers claim the next item (an abandoned claim is freed after 30 minutes), give one score per config of the queue, skip or release it, and can work entirely from the keyboard.
- **Datasets.** Build a dataset from traces or upload `.jsonl` and `.csv` files (up to 5 MB and 10 000 items per dataset), with bad lines reported by line number instead of failing the file. Items can be edited, and the recorded output of a trace is offered only as a starting point for the expected output.
- **Evaluators.** Score traces automatically: heuristic rules (`regex`, `json_valid`, `length`, `contains`, `exact_match`, `levenshtein`), an LLM judge with four built-in templates (`relevance`, `faithfulness`, `toxicity`, `format_compliance`; every score is 0 to 1, higher is better) or your own chat prompt, and signed webhook evaluators. Online sampling runs every minute over traces that ended 30 seconds to 5 minutes ago, never samples Spanlight's own traces (`evals`, `playground`, `experiments` and `doctor` environments, and judge calls), and queues each trace at most once.
- **Judge budgets.** A judge needs a monthly budget and reserves the most a call could cost before it makes the call, under a lock, so concurrent runs cannot both pass the limit; a run that does not fit is marked `budget_exceeded` and sends no request. The budget covers sampled runs, tests and experiment use, and a meter shows spent, reserved and remaining.
- **Experiments.** Replay a dataset through a prompt version or typed messages, score every answer, and compare two runs item by item with deltas, an improved and regressed count, cost and p95 duration. A run can override the prompt version, so one experiment compares versions of a prompt. A cost limit reserves the estimate of every call in flight, so a run never starts an item that could pass it.
- **Eval gate.** The `eval-gate` GitHub Action and `python -m spanlight._experiments` run a prompt over a dataset in CI and fail the build when a mean score falls below a threshold.
- **Demo.** `spanlight demo seed-evals` creates a prompt with two versions, a 50-item dataset from the demo's recorded traffic, a budgeted judge and a two-run experiment.
- **Screens and navigation.** Prompts, Playground, Datasets, Review, Evaluators and Experiments under a new Improve group in the sidebar (`G P`, `G L`, `G N`, `G Q`, `G E`, `G X`), scores on trace and span detail, and a Scores column on Traces.
- **Roles and audit.** Members can create prompts, score configs, datasets, annotation queues and experiments, add scores and start experiment runs; evaluators are admin and owner only, because a judge spends money unattended. The main changes to them are recorded in the audit log (`prompt.*`, `score_config.*`, `dataset.*`, `annotation_queue.*`, `evaluator.*`, `experiment.*`).
- **Operations.** New Prometheus metrics (`spanlight_scores_written_total`, `spanlight_prompt_reads_total`, `spanlight_evaluator_runs_total`, `spanlight_evaluator_cost_usd_total`, `spanlight_experiment_items_total`) feed an Evaluations row on the Grafana dashboard. Prices include `claude-haiku-5-5`, the model the demo uses.

### Changed

- **The `prompts:read` and `scores:write` key scopes now do something.** They were accepted but reserved. Existing keys keep their scopes, so a key made for sending traces cannot read prompts or send scores: create a new key with the scope.
- **Native JSON ingestion accepts `prompt_version_id` on a span.** Older servers ignore it. OTLP has no field for it.
- **Trace summaries carry `has_scores` and a few latest `scores`**, and `GET .../traces/filters` lists `score_names`.
- **Dataset uploads may be 5 MB.** Every other `/api/v1` body is still limited to 1 MiB; the limit for the upload route is checked while the body streams.
- **The landing page covers the whole product and shows the real one.** A new "Beyond tracing" section presents the Doctor, the gateway, prompts and playground, evals, and alerts and budgets, each with a real screenshot from the live demo; the hero shows the real experiment comparison instead of a drawn preview with made-up numbers. Screenshots follow the site theme and are captured by `frontend/scripts/capture-landing.ts`. The copy now covers prompts, scores, the CI eval gate and `pip install spanlight`. The nav links the quickstart and GitHub, and the rebuilt footer links Product, Developers, Guides and Project pages (evaluators and experiments are separate guides now); a test checks that every documentation link points at a page that exists.
- **Possibly breaking: model names are priced case-sensitively, and surrounding whitespace is stripped at ingest.** A span's `model` is stored without the whitespace around it (`" gpt-4o "` becomes `gpt-4o`, a whitespace-only name becomes `null`), and its case is kept. Pricing now matches the stored name exactly, as alert rules, rollup filters and budgets already did, so a model sent in another case than the provider's id (`GPT-4o`, `Claude-Sonnet-4-5`) costs `null` ("—") and is listed among the project's unpriced models instead of being priced while budgets and alerts on `gpt-4o` missed it. Send the provider's own model id to keep costs.
- **OTLP keeps a span whose resource or token values do not fit.** A `deployment.environment` over 64 characters or a `service.version` over 128 is dropped instead of rejecting every span of the service; a token count sent as a whole double (`12.0`) is stored as `12`, and one with a fraction, NaN or an infinity is dropped (the span is kept with the count unknown and its cost `null`). One `otlp_values_dropped` warning per batch logs how many values were dropped, never the values. Native `/v1/traces` still rejects such spans.

### Fixed

- **A gateway span whose write fails is retried instead of lost.** A span the database refused was dropped after one try, so a paid call could leave no span and recorded spend (and the budgets evaluated on it) came out low. A write that fails for a passing reason (a lost connection, a timeout, a deadlock) is now retried after 0.1, 0.5 and 2 seconds, and the write backlog (`GATEWAY_RECORD_BACKLOG`) grows from 1000 to 5000; a waiting span holds its input and output already redacted and cut to 32 KB, so a waiting span's payloads are at most 64 KB of JSON as stored (the memory it takes in the process is larger, so size the backlog by what the api can spare). A span still not stored is counted in `spanlight_gateway_record_failures_total` as before and in the new `gateway_spans_lost` field of `/health/ready`, which never makes the probe fail. A span whose input or output cannot be prepared for the queue is counted the same way instead of only being logged.
- **A gateway call whose body holds NUL, a lone surrogate, NaN or Infinity keeps its span.** Plain `json.loads` accepts all four and Postgres stores none of them, so the call was answered but its span was refused (and, for a lone surrogate, the call itself failed with a 500). The span now stores the body with NUL removed, each lone surrogate replaced by `?` and each non-finite number as its name; a lone surrogate is forwarded as the `\uXXXX` escape the client sent; and a `model` holding NUL or a lone surrogate is `400 INVALID_REQUEST` instead of a 500. A span write the database refuses outright is no longer retried for 2.6 seconds, and its log line names the error type and SQLSTATE, never the message.
- **A Lab `truncated_stream` call is recorded as failed.** The client still gets the first frames and a clean end of stream without the final event, but the span, which said `ok`, now has status `error` and the message `200 FAULT_TRUNCATED_STREAM: …`, like a `malformed_json` call. A stream too short to cut passes whole and stays `ok`.
- **A gateway call cancelled while the provider was answering now shows the attempt it made.** Its span reported `spanlight.gateway.attempts` 0 and no provider, so a request the provider had received, and may bill, left no trace of the attempt. The span now lists it, and `spanlight_gateway_attempts_total` counts it as `cancelled`.
- **A card number next to other digits is redacted.** A card followed or preceded by a CVV, an expiry or a quantity (`4242 4242 4242 4242 123`, `qty 2 4242424242424242`) was read as one longer number that fails the Luhn check, and was stored whole. Card-shaped runs (groups of four or more digits, the last of two or more, separated by single spaces or dashes) are now searched for 13-19 digit cards made of whole groups; the neighbouring digits are kept, and lists of small numbers, dates and phone numbers are left alone. A card typed in groups of one to three digits is not detected.
- **An ingestion client that disconnects mid-body gets `400 BAD_REQUEST`** ("The request body was not fully sent.") on `/v1/traces`, `/v1/otlp/traces` and `/v1/scores`, and the gateway answers `400 INVALID_REQUEST`, instead of being logged as an unhandled server error.
- **A gzip or deflate body that is cut short is refused** with `400 BAD_REQUEST` (`400 INVALID_REQUEST` on the gateway) instead of being accepted when the part that arrived happened to parse. An empty body sent as gzip or deflate gets the same answer; on ingestion it was `400 INVALID_JSON`.
- **Evaluator run and test scores send `value` as a JSON number**, as trace scores and the documented contract already did; it was a decimal string (`"0.8"`).
- **A dataset upload full of unparseable lines reports at most 100 rejected lines**, like a file of invalid items already did; every bad JSON line used to be listed, so a 5 MB file could produce a response many times its size.
- **Rolling back below migration 0505 with evaluator scores no longer blocks the next upgrade.** The downgrade now clears `scores.evaluator_run_id` before it drops the evaluator runs.
- **Migrations validate every row of a table under row-level security.** Run as the table owner, a migration validated a foreign key with a query that row-level security filtered, so migration 0505 could mark `scores.evaluator_run_id` valid while a score pointed at a missing run. Every `VALIDATE CONSTRAINT` now runs with the worker bypass set for its own transaction, and such a dangling reference fails the upgrade.
- **SDK: `Prompt.compile` accepts a variable value holding a lone surrogate** (as `os.fsdecode` produces), and a fallback template holding one is an `InvalidTemplateError` (`PromptUnavailableError` from `prompts.get`) instead of a `UnicodeEncodeError`.
- **SDK: a `Retry-After: nan` or `inf` header is ignored** and the exporter waits its normal backoff instead of retrying at once.
- **Starting a review no longer ends the session while items are pending.** Opening work mode sent two claims at once (the page mounts twice while the app switches to its focus layout), the second found the first one's item locked and the page said "All done". The app now picks its layout from the route it is about to show, so the page mounts once, and a remounted page waits for the claim already on its way.
- **The new-experiment form says when a list fails to load.** A failed dataset list left the field loading forever and a failed credential list showed an empty select; datasets, credentials, prompts and evaluators now each show "Couldn't load the …" with **Try again**.
- **Release pickers stop loading when the release list fails**: both selects are disabled and say "Couldn't load releases".
- **No more Content-Security-Policy error in the console on every page.** zod built schemas before its no-`eval` setting applied; the setting now loads with zod itself.
- **The dataset upload preview counts only what the server will store.** A bare carriage return outside quotes, or a cell over 65,536 characters, stops the server's CSV reader; the preview now marks that line "unreadable record; the rest is skipped" and counts nothing after it.
- **Email channels accept internationalized addresses** such as `ops@例え.jp`, which the API already accepted.
- **A free-text categorical score of up to 200 characters is accepted when it holds emoji or other characters outside the Basic Multilingual Plane**; the form counted UTF-16 units instead of characters.
- **Experiment comparisons never show "+0.0000" or round a pass rate up to "100%".** A score change below 0.0001 reads "+<0.0001", and a yes/no pass rate rounds down, so "100%" means every item passed. An evaluator's budget meter rounds down the same way, so it reads "100%" only once the budget is used up.
- **Accessibility: the audit log's filter pills, the Lab probability slider and the dataset file picker have accessible names.**
- **The landing page no longer captions a missing screenshot as "A real screen from the live demo".**
- **Layout fixes at 375 px and on short desktop windows.** On a phone the account pages' "Back to app" becomes an icon button instead of sliding under the theme toggle, the span detail tabs wrap into two rows instead of overlapping, a trace's start time wraps instead of being cut off, and gateway KPIs shrink together to fit instead of showing "100. …". In the sidebar only the page list scrolls, the current page is scrolled into view, and fades show when more pages are above or below.

### Security

- A judge's input is passed as marked data and the model is told to treat it as data, which reduces and does not remove prompt injection. Scores are advice, not proof; this is stated in the documentation.
- Judge spend is bounded by a reservation per evaluator rather than a check after the fact, and an unpriced model never runs.
- Webhook evaluators use the same vetted client as alert webhooks (https only, no private addresses unless `WEBHOOK_ALLOW_PRIVATE_TARGETS`, no redirects), are signed with the channel's secret, make one attempt with a 30 second limit, and never put the URL or the secret in a run's error or a log line.
- A `regex` rule runs with a 1 second time limit on at most 10 000 characters, so a pattern that backtracks without end fails its run instead of holding a worker.
- Dataset files are parsed with fixed limits (5 MB, 64 KB per value, 10 000 items), in a thread pool, and an error never repeats the content of a line.
- A malformed backup database URL (for example one without a host, where the password reads as the port) is refused with a fixed message; the error used to quote the bad port, which could be the password.
- "Explain with Claude" no longer turns off row-level security inside the request to check the organization's budget. The sum is a narrow database function, and the reservation is written under the request's project like any other row.
- The new tables are protected by row-level security like traces: child tables (prompt versions, labels, dataset items, queue items, evaluator runs, experiment runs and items) are filtered through their parent. Scores are keyed by project and id, so a client-chosen id cannot collide with, or hide behind, another project's.

### SDK

- **`spanlight.prompts.get(name, label="production", *, version=None, fallback=None)`** fetches a prompt with a revalidating cache. After the cache time it serves the stale copy if the server fails, then the `fallback`, and raises `PromptUnavailableError`, the SDK's one deliberate exception, only with neither. `Prompt.compile(**variables)` follows the server's template rules and raises `MissingVariablesError` or `InvalidTemplateError`.
- **Span linking.** `spanlight_prompt=compiled` on `wrap_openai` and `wrap_anthropic` calls, `with compiled:` for everything inside a block, and `spanlight.no_prompt()` to keep a task unlinked.
- **`spanlight.score(trace_id, name, value, *, span_id=None, comment=None)`** sends a score in the background with the spans, with an id per call so a retry cannot store it twice. It never raises.
- **Experiment runner** `python -m spanlight._experiments`, behind the eval-gate Action.
- **Python SDK 0.1.1.** The eval-gate report prints p50 and p95 latency in whole milliseconds, and its "Open the run" link and the Action's `run-url` output open the run page instead of the experiment.

### Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `EVALS_ENABLED` | `true` | Schedule online evaluation. Off, evaluators run only in experiments and tests. |
| `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` | `50` | Most runs one evaluator gets per one-minute sampling cycle. |
| `EXPERIMENT_MAX_ITEMS` | `1000` | Ceiling for an experiment's `max_items`. |
| `GATEWAY_RECORD_BACKLOG` | `5000` (was `1000`) | Most gateway spans waiting to be written, retries included. |

### Migrations

Run `spanlight migrate` and then `spanlight ensure-app-role` (or restart the stack) after upgrading. Migrations 0500 to 0506 add prompts, versions and labels, the span-to-version link, scores and score configs, datasets, annotation queues, evaluators and their runs, and experiments, runs and items. Migration 0507 adds `explain_month_to_date_cost`, the function the explain budget sums with; `ensure-app-role` grants the app role permission to call it. Migrations 0501 and 0502 build their indexes on `spans` and `traces` `CONCURRENTLY`: writes continue, and on a large table the build takes a few minutes and extra disk. 0502 also adds `traces.has_scores`; a retry of a failed `CONCURRENTLY` build drops its half-built index first.

## [0.5.0] - Unreleased

The Doctor: Spanlight reads your traces, opens findings about how your application calls LLMs, and shows what changed between releases and who drives cost.

### Added

- **Error class, request hash and finish reason on LLM spans.** Every failed span gets one class (`auth`, `rate_limit`, `timeout`, `context_length`, `content_filter`, `provider_5xx`, `network`, `client` or `unknown`) from its HTTP status or message, and Traces can be filtered by it. Spans carry a `request_hash` (the same model and request give the same hash, computed before payload capture so it exists even when inputs are not stored) and a canonical `finish_reason` (`stop`, `length`, `tool_calls`, `content_filter` or `other`) for OpenAI, Anthropic and OTLP spans. Gateway spans for faults that answer before the provider call now record the simulated status and `Retry-After`. Spans stored before this release have none of these and are not backfilled.
- **Fifteen detectors.** Deterministic rules over a project's recent spans: `error_spike`, `latency_regression`, `cost_spike`, `retry_storm`, `retry_after_ignored`, `rate_limit_pressure`, `truncated_outputs`, `context_growth`, `cache_opportunity`, `tool_loop`, `truncated_stream_accepted`, `unsupported_parameter_retried`, `client_timeout_misconfigured`, `unpriced_spend` and `provider_incident`. They run every 15 minutes for each project with traffic in the last 24 hours, skip calls the Integration Lab faulted when they judge traffic or provider health, and return nothing below a minimum sample size. Each finding carries its evidence (up to 20 traces), the layer where it fails, whether it is measured or inferred, a suggested fix and a way to verify it. The catalogue, thresholds and fixes are on the new Doctor page of the documentation.
- **Insights lifecycle.** A problem is one insight however often it recurs: it counts occurrences, resolves itself after 24 hours without a sighting, and reopens if it returns. Admins and owners can acknowledge, resolve, mute (up to 90 days, with a reason) and unmute. A project's insight notifications go to a list of its organization's alert channels; only a critical insight that opens notifies, once, as an `insight.opened` webhook event (signed like alert events), a Slack message, an email or a PagerDuty trigger. The weekly digest gains an Insights section.
- **Health score.** Overview shows a 0 to 100 score: 100 minus penalties for open critical and warning insights, error rate, a slower p95 and higher spend than the previous window. It is "—", never 0, when the window has no LLM calls.
- **Explain with Claude.** An opt-in button on an insight asks Claude to explain the finding from its evidence. Admins and owners only, capped per organization at `EXPLAIN_MONTHLY_BUDGET_USD` a month with the cost reserved up front, shown under an "Advisory" label as plain text. Only redacted excerpts of at most five example traces are sent, through the organization's own Anthropic credential and traced into the project.
- **Releases.** Set a release with the SDK, the OTLP `service.version` attribute or the gateway header `x-spanlight-release`. The Releases page lists releases in a range and compares two: deltas, model mix, error classes and new error messages, with exact percentiles over a range of up to 30 days.
- **Users.** The Users page ranks end users (the SDK `user_id`, OTLP `user.id` or `x-spanlight-user`) by cost, errors or traces over whole UTC days, with a daily chart and recent sessions for each. Statistics are refreshed every 15 minutes. Releases and Users can also be read with a project API key that has the `traces:read` scope.
- **Lab example clients.** `examples/` holds a deliberately naive client and a corrected one that run the Integration Lab scenarios against the gateway. The naive client opens the matching finding for each scenario and the corrected one is written to open none.
- **Doctor screens.** Doctor (list and detail with evidence, fix, verification and actions), Releases, Users, a health tile on Overview, insight badges on traces, an error-class filter on Traces and an Insight notifications setting in Settings, Project.
- **Operations.** `GET /api/v1/projects/{id}/detector-runs` shows what each detector did on its last passes. New Prometheus metrics (`spanlight_detector_runs_total`, `spanlight_detector_duration_seconds`, `spanlight_insights_open`, `spanlight_detector_projects_skipped_total`) feed a Doctor row on the Grafana dashboard. A new runbook covers detector runs, a project that times out, muting and the explanation budget.

### Changed

- **`insight.opened` joins the notification payloads.** Webhook, Slack, email and PagerDuty channels render it next to alert events; PagerDuty uses a dedup key that is stable for the insight and never sends a resolve for one.
- **The gateway reads `x-spanlight-release`** (1 to 128 characters) into the trace, so release comparison covers traffic that has no SDK.
- **Gateway `Retry-After` handling.** The provider's `retry-after-ms` header is now passed back to the client next to `retry-after`, and the gateway honours and records the millisecond value first.
- **A Lab `malformed_json` call is recorded as failed.** It still answers `200` with the cut body, but its span has status `error` and the message `200 FAULT_MALFORMED_JSON: …`, so a client's reaction to it is judged like any other failure.

### SDK

- **Request hash and finish reason on LLM spans.** The OpenAI and Anthropic wrappers now send a `request_hash` (a stable hash of the model and request parameters, computed before payload capture so it exists even when inputs are not stored) and the provider's raw `finish_reason` (the first choice's `finish_reason` for OpenAI, `stop_reason` for Anthropic, streams included). Manual spans can call `span.set_request_hash(...)` and `span.set_finish_reason(...)`.

### Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `DETECTORS_ENABLED` | `true` | Schedule the detector job. Off, no new insights appear. |
| `EXPLAIN_MODEL` | `claude-sonnet-5-5` | The model behind "Explain with Claude". It must have a price. |
| `EXPLAIN_MONTHLY_BUDGET_USD` | `1.00` | Most an organization may spend on explanations per UTC month. `0` turns them off. |
| `USER_STATS_ENABLED` | `true` | Schedule the refresh of per-user daily statistics behind the Users page. |

### Migrations

Run `spanlight migrate` (or restart the stack) after upgrading. Migrations 0400 to 0405 add the error class, request hash and finish reason columns on spans, the insights, detector runs and insight explanations tables, the project's insight channel list, the per-user daily statistics table and a release index on traces. Migrations 0400, 0401 and 0405 build their indexes `CONCURRENTLY`: writes continue, and on a large table the build takes a few minutes and extra disk.

## [0.4.0] - Unreleased

Alerts and budgets: get told when error rate, latency, volume or cost crosses a line, and stop runaway spend at the gateway.

### Added

- **Alert channels.** Organization-wide channels for email, Slack incoming webhooks, signed webhooks and PagerDuty (Events API v2). Email goes only to verified members of the organization. A channel can be tested, and its delivery log shows every send with its status, attempts and last error; a failed delivery can be retried by hand. Slack URLs, PagerDuty routing keys and webhook signing secrets are encrypted with `CREDENTIALS_KEYS`, never returned, and a webhook's secret is shown once, when it is created or rotated.
- **Signed webhooks.** Each request carries `X-Spanlight-Event`, `X-Spanlight-Delivery`, `X-Spanlight-Timestamp` and an HMAC-SHA256 `X-Spanlight-Signature`, with a documented verification procedure and test vector. Failures are retried with backoff for up to 8 attempts.
- **Alert rules.** Threshold rules and anomaly rules (a band of a chosen number of standard deviations around the average of the previous windows) on error rate, p95 latency, p95 time to first token, LLM calls, tokens and cost, filtered by environment, provider or model, with a 7-day preview before you save. Rules are evaluated every 60 seconds with a cooldown after each resolve, can be muted for up to 30 days, and their events can be acknowledged. A metric with no data never fires or resolves a rule.
- **Budgets.** Daily or monthly (UTC) spend limits per project, gateway key, end user or model, each set to notify or to block. A budget notifies its channels when its spend passes the amount and again when it recovers.
- **Blocking budgets.** The gateway answers `402 BUDGET_EXCEEDED`, in the OpenAI or Anthropic error shape, to calls in the scope of a blocking budget that is exceeded. Ingestion is never blocked. Blocking lags spend by up to one evaluation, about a minute.
- **Weekly digest.** Every Monday at 08:00 UTC, members with a verified email get one summary per project of spend, LLM calls, error rate, p95 latency, top models by cost and alerts fired. It can be turned off per project in project settings and needs an email provider.
- **Dashboard screens.** Alerts (Rules and Channels) and Budgets, with the rule editor and preview, mute and acknowledge, the event timeline, and the delivery log.
- **Grafana alert panels.** Rules evaluated by outcome, transitions by kind, evaluation duration, outbox backlog, deliveries by kind and outcome, and budget blocks. `/health/ready` reports `alert_evaluation_lag_s`.
- **`spanlight alerts evaluate`.** Runs one evaluation pass by hand, with `--dry-run` to roll it back and `--json` for scripts, which is also how the evaluation load test is timed.
- **Documentation.** Guides for alerts and channels, budgets and the weekly digest, plus a runbook for stuck and failed deliveries, a rotated webhook secret and late evaluation.

### Changed

- **Outbox leases and fencing.** A worker now claims an outbox row with a 60 second lease and a fencing token and sends outside any database transaction. A worker whose lease expired can no longer settle the row, and a crashed worker's rows are claimed again by the next run. Delivery is at least once.
- **Failed alert payloads are kept.** Payload reduction after a delivery settles now keeps the payload of a failed alert delivery so it can be retried; sent rows and failed transactional mail (verification, reset, invite) are reduced as before.
- **Egress protection is a shared module.** The check that refuses private, loopback, link-local and reserved addresses on every connection moved from the gateway into `app/core/egress.py` and now also guards webhook, Slack and PagerDuty deliveries.
- **Outbound URLs stay out of logs.** `httpx` and `httpcore` log at WARNING and Sentry's `httpx` integration is off, so a Slack or webhook URL can never reach a log line or an error report.
- **Model filters match snapshot names.** An alert or budget filter on `gpt-4o` also matches `gpt-4o-2024-08-06` and `gpt-4o-latest`, but not `gpt-4o-mini`, the same rule the price table uses.
- **SMTP on port 465.** With `SMTP_PORT=465` the email sender connects with implicit TLS (SMTPS) and checks the certificate, for networks that block port 587.
- **The gateway budget guard is real.** The hook added in 0.3.0 now reads the evaluated state of blocking budgets instead of allowing every call.

### Security

- Alert email cannot be used to write to strangers: recipients must be verified members, checked when the channel is saved and again when the message is queued (`ALERT_EMAIL_ANY_RECIPIENT` lifts it for a closed deployment).
- Webhook URLs must be `https://` and resolve only to public addresses, checked when the channel is saved and on every connection, so a DNS change after the check does not get around it. Redirects are never followed, and response bodies are capped before they reach `last_error`. `WEBHOOK_ALLOW_PRIVATE_TARGETS` lifts both rules for a receiver inside a private network.
- Channel secrets are never written to a log, the audit log or `last_error`.

### Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `ALERTS_EVALUATION_ENABLED` | `true` | Schedule the once-a-minute alert and budget evaluation. |
| `WEEKLY_DIGEST_ENABLED` | `true` | Schedule the Monday digest email. |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | `false` | Allow `http://` and private addresses for webhook channels. |
| `ALERT_EMAIL_ANY_RECIPIENT` | `false` | Allow email channels to send to addresses that are not verified members. |
| `PAGERDUTY_EVENTS_URL` | `https://events.pagerduty.com/v2/enqueue` | Where PagerDuty channels send events. |

### Migrations

Run `spanlight migrate` (or restart the stack) after upgrading. Migrations 0300 to 0307 add the outbox lease columns, the alert channel, rule, state, event and budget tables, the weekly digest switch on projects, and three indexes. Migrations 0302 (end-user traces), 0306 (a channel's deliveries) and 0307 (weekly digest lookups) build their indexes `CONCURRENTLY`: writes continue, and on a large table the build takes a few minutes and extra disk.

## [0.3.0] - Unreleased

The LLM gateway and the Integration Lab: point the official OpenAI or Anthropic client at Spanlight with one base URL and every call is traced, priced, limited and, if you configure it, routed, retried, cached and fault-injected.

### Added

- **LLM gateway.** `POST /gw/v1/chat/completions`, `POST /gw/v1/responses`, `POST /gw/v1/messages` and `GET /gw/v1/models` accept OpenAI and Anthropic requests, streaming and tool calls included, and forward the provider's answer unchanged. Errors come back in the provider's own shape with a stable `spanlight_code`. Each call is recorded as one `llm` span with the gateway's route, target, attempts and overhead, written after the answer is sent (a span that cannot be written is counted in `spanlight_gateway_record_failures_total`), and can join a trace you already started with the `x-spanlight-*` headers.
- **Provider credentials.** Organization owners store OpenAI, Anthropic and OpenAI-compatible API keys. Keys are encrypted with `CREDENTIALS_KEYS`, never returned, and can be checked, rotated and re-sealed under a new master key with `spanlight reseal-credentials`. The gateway refuses to connect to private, loopback, link-local and other reserved addresses, on every connection, unless `GATEWAY_ALLOW_INSECURE_BASE_URLS` is set for a local model server.
- **Routes.** Weighted targets, per-target model aliases, retries with backoff and `Retry-After`, fallbacks to the next target, and a time budget for the whole call. Every save is a numbered version with revert, and a stale edit is refused with `409 ROUTE_VERSION_CONFLICT`.
- **Gateway keys and limits.** Project-scoped `spl_gw_…` keys, accepted as a bearer token or `x-api-key`, with a route, an environment, requests and tokens per minute, an allowed-model list and default tags. `GATEWAY_ORG_RPM_CEILING` caps an organization across its keys.
- **Response cache.** Opt-in per key with a time to live. Exact match on the surface, route, route version and request body, for non-streaming `200` answers up to 1 MB. `X-Spanlight-Cache` reports `hit`, `miss`, `off` or `bypass`, and a project's cache can be purged.
- **Budget guard hook.** The gateway asks a budget guard before every provider call and answers `402 BUDGET_EXCEEDED` when it says no. The guard that ships allows every call; budget rules arrive in a later release.
- **Price overrides.** An organization can set its own per-model prices (`/api/v1/orgs/{org_id}/price-overrides`). They win over the built-in table for spans ingested afterwards, and such spans record `pricing_version` `override:<id>`.
- **Integration Lab.** Fault profiles inject nine provider failures into non-production keys: expired or denied keys, rate limits, an unsupported parameter, server errors, a cut-off JSON body, a stream that ends early, a slow first byte and a timeout. Faulted calls are marked by `X-Spanlight-Fault`, a `FAULT_<SCENARIO>` code and a `lab:<scenario>` trace tag.
- **Standalone gateway.** `GATEWAY_MODE=standalone` runs the gateway as its own process (`python -m app.gateway`, same image) behind the `standalone-gateway` Compose profile, so LLM traffic can scale and fail apart from the dashboard API. `disabled` turns it off. There is a new runbook for it.
- **Demo through the gateway.** The demo project's LLM calls now go through the gateway with the demo project's own key, so the demo exercises the same path as a real application.
- **Gateway load test.** The k6 load tests gain a scenario that measures the time a call spends in Spanlight against a fake provider, with a target of under 20 ms at p95, and `docs/performance.md` explains the method.
- **Dashboard screens.** A Gateway section with Overview (traffic, errors, cache hit rate, fallbacks and retries per target), Keys, Routes with a versioned editor, Credentials and Lab. New Prometheus metrics cover gateway requests, overhead, upstream time and attempts; Grafana panels show requests, overhead, attempts, cache hits and spans not recorded.
- **Documentation.** Gateway guides for the quickstart, routing, the cache, the Lab, credentials and error responses.

### Changed

- **`CREDENTIALS_KEYS` now also protects provider credentials.** Rotate it in two phases and run `spanlight reseal-credentials` before dropping an old key (see the rotate-secrets runbook).
- **Audit log.** Gateway configuration changes (credentials, routes, keys, fault profiles, cache purges and price overrides) are recorded as audit events.

### Security

- Provider API keys are write-only: they are never returned by the API, written to a log or kept in an `Idempotency-Key` record, and responses that describe a credential carry `Cache-Control: no-store`.
- Gateway keys are stored as hashes, compared in constant time, and redacted from logs and spans.
- The gateway never forwards the client's `Authorization`, `x-api-key`, cookies or `x-spanlight-*` headers to a provider, does not follow redirects, and sends no CORS headers.
- Fault injection cannot run on a key whose environment is `production`, checked when a profile is attached and again on every call.

## [0.2.0] - Unreleased

Production hardening: accounts and access control, a versioned API, durable background work, backups and exports, observability of Spanlight itself, and documentation.

### Added

- **Accounts.** Email verification and password reset by email (links carry the token in the URL fragment; verification links expire after 24 hours, reset links after 1 hour). Sign in with GitHub and Google. Two-factor authentication with an authenticator app and recovery codes, which an organization owner can require for all members.
- **Access tokens.** Personal access tokens (`spl_pat_…`) for scripts and CI, with a `read` or `write` scope and an optional expiry, revocable at any time.
- **API keys.** Project API keys carry scopes (`ingest:write`, `traces:read`) and an optional expiry. Existing keys keep `ingest:write` and never expire.
- **Idempotency keys.** State-changing API requests accept an `Idempotency-Key` header, so a retried request runs once.
- **Dashboard at scale.** Hourly rollups with bucketed latency histograms back every time window longer than 24 hours; those responses are marked `"approximate": true`. Shorter windows still read the spans directly.
- **Shared rate limits.** Limits for ingestion, the demo and bearer reads are enforced in Postgres, so they hold across replicas and across restarts. A limited request gets `429` with `Retry-After`.
- **Object storage and backups.** Any S3-compatible store (AWS S3, Cloudflare R2, MinIO) can hold nightly `pg_dump` backups, kept for 14 daily and 8 weekly copies, and `spanlight restore` loads one back.
- **Exports.** Export traces as CSV or JSONL, and the audit log as CSV. Download links last one hour and files are deleted after seven days.
- **Price endpoints.** `GET /api/v1/prices` lists the price table, and `GET /api/v1/projects/{project_id}/unpriced-models` shows which models in your traffic have no known price.
- **Organization and project administration.** Rename or delete an organization or a project through the API, with a typed confirmation for deletes.
- **Observability of Spanlight itself.** Optional self-tracing with OpenTelemetry, a readiness check (`/health/ready`) that reports the database, the migrations and the worker, and Prometheus metrics for the worker (jobs, outbox, notifications, rollups) on `WORKER_METRICS_PORT`.
- **Operations.** Runbooks for deploying, rolling back, restoring, rotating secrets, revoking keys, scaling, ingestion spikes and service level objectives, plus a threat model and security documentation.
- **Documentation site.** Quickstart, Python SDK, OTLP, self-hosting, configuration, API reference, security, runbooks and this changelog, served at `/docs/` by the web image.
- **Load tests.** k6 scripts and a seeder in `backend/load/` measure ingestion (500 spans a second), the dashboard overview and the trace list (10 million spans) against the Compose stack, and a manually started workflow runs them on a GitHub-hosted runner. Method and targets are in `docs/performance.md`.

### Changed

- **Versioned API.** Dashboard routes moved from `/api/<resource>` to `/api/v1/<resource>`. The old paths return `404`. Ingestion (`/v1/traces`, `/v1/otlp/traces`) keeps its own version and is unchanged. A future breaking change will ship as `/api/v2`, with `/api/v1` kept for at least 12 months.
- **Postgres shared memory.** The Compose `postgres` service gets 256 MB of `/dev/shm` instead of Docker's 64 MB default, which parallel queries on large tables could exhaust ("could not resize shared memory segment").
- **Dashboard queries at 10 million spans.** Migration 0016 swaps the `spans` time index for a covering one, so a 24 hour overview is answered from the index and not from the table. It is built `CONCURRENTLY`: writes continue, and on a large table it takes minutes and extra disk.
- **Row-level security policies read their settings once per statement.** Migration 0017 rewrites the tenant-isolation and worker-bypass policies of `traces`, `spans`, the rollup tables and `exports` so `current_setting()` is evaluated once per query instead of once per row; which rows are visible does not change. It takes a brief exclusive lock on each table and gives up after 5 seconds if it cannot get it, so run it again at a quieter moment.
- **A busy database answers `503`, not `500`.** A request that waits more than 5 seconds for a connection (`API_POOL_TIMEOUT_SECONDS`) or has a statement that runs longer than 10 seconds (`API_STATEMENT_TIMEOUT_SECONDS`) now gets `503 SERVICE_UNAVAILABLE` with `Retry-After: 5`.
- **Postgres JIT off in Compose.** The `postgres` service starts with `jit=off`, which costs more than it saves on the dashboard and rollup queries.
- **More than one replica.** Rate limits and idempotency state live in Postgres, so the API can run as several replicas.
- **Optional features report themselves.** A request that needs an unconfigured integration (email, object storage, OAuth, `CREDENTIALS_KEYS`) answers `409 NOT_CONFIGURED` and names the setting; a background job that needs one ends as `skipped_not_configured`.

### Security

- Two-factor seeds are encrypted at rest with the keys in `CREDENTIALS_KEYS`.
- Sign-up is throttled per client IP.
- Password hashing and checking (argon2) run off the event loop, on a small dedicated executor that caps how many hashes run at once, so a burst of sign-ins cannot starve other requests or exhaust memory.
- Requests to `/api/v1` are limited to 1 MiB (`413` above that). Deeply nested JSON sent to ingestion answers `400 INVALID_JSON` instead of `500`.
- `BACKUPS_ENABLED` and `BACKUP_DATABASE_URL` are set only on the worker, the only service that runs backups. The object storage keys stay on both services, because the API signs export download links.
- Responses that reveal a secret (a new API key or access token, recovery codes, a two-factor setup secret) carry `Cache-Control: no-store`.
- The `/metrics` token is compared in constant time.
- The web image runs Caddy as a non-root user.
- Export files are written under a separate key for each attempt, so a retry or a lease takeover can never delete a file another attempt finished. Download links force the browser to save the file instead of showing it.
- A database that is too busy to answer answers `503` with `Retry-After`, not `500`.
- Row-level security policies read their settings once per query. Which rows are visible does not change.

## [0.1.0]

Initial version.

### Added

- **Tracing.** Python SDK (`spanlight`) with `@observe`, spans and wrappers for the OpenAI and Anthropic clients, native JSON ingestion (`POST /v1/traces`) and OTLP ingestion (`POST /v1/otlp/traces`, JSON and protobuf, OpenTelemetry GenAI attributes).
- **Dashboard.** Overview with latency, token, cost and error metrics, a trace explorer with a span waterfall, and session views.
- **Cost.** Versioned per-model prices; a model without a known price shows as unknown, never as $0.
- **Teams.** Organizations, projects, roles, invites, API keys and an audit log.
- **Privacy.** Postgres row-level security, secret redaction before storage, and per-project switch for payload capture.
- **Self-hosting.** One Docker image with `api`, `worker` and `web` roles, a Compose file and a Railway deployment guide.
