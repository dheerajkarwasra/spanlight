# EGM drift report, 2026-11-09

The 127 EGM.md contracts generated on 2026-11-09, checked against the code at commit e1063519 by ten fresh reviewers that did not write them. Each section lists only differences; "No drift detected" means the contract matched.

## backend/app/EGM.md

- Tenancy layer 2 is incomplete. The document lists only 404 for non-members, 403 for missing permissions, `TOKEN_SCOPE` and `KEY_SCOPE`. `require` also refuses members of an organization with `require_2fa` who have not enabled TOTP, with 403 `TWO_FACTOR_REQUIRED`. This check runs after membership and before the permission check (`backend/app/api/deps.py:564`). Refused changes are written to the audit log through `_audit_denied` with reasons `forbidden`, `two_factor_required` and `token_scope` (`backend/app/api/deps.py:575`). An opted-in API key also needs the `traces:read` scope (`backend/app/api/deps.py:516`). The document mentions none of these.
- CSRF is never described. The document says the standalone gateway has "no cookies, CSRF, Origin check", but for the api it lists only `OriginCheckMiddleware`. The double-submit CSRF check on unsafe methods (header against cookie, plus `verify_csrf_token`, which returns 403 `CSRF_FAILED`) lives in `backend/app/api/deps.py:authenticate_session` (lines 141-149), and the document never points there.
- The list of cases in which `Settings` refuses to load is incomplete. `_pagerduty_url_is_https` also fails startup unless `PAGERDUTY_EVENTS_URL` is an `https://` URL with a host and no userinfo (`backend/app/config.py:459-467`).
- The settings groups leave out several security-relevant switches: `webhook_allow_private_targets` (`backend/app/config.py:221`), `alert_email_any_recipient` (line 225), `pagerduty_events_url` (line 227), `explain_model`/`explain_monthly_budget_usd` (line 238), `replay_monthly_budget_usd` (line 260), `audit_retention_days` (line 269) and `gateway_record_backlog`/`gateway_record_backlog_mb` (line 211).
- The document says `server.py:run_application` serves with `proxy_headers=True`. It leaves out `forwarded_allow_ips="*"` (`backend/app/server.py:166`). That setting makes uvicorn trust `X-Forwarded-For` from any peer, and per-IP login throttling and rate limits depend on it, so the api must only be reachable through Caddy.
- `run_application` exits with `SystemExit(3)` when the server never started and no exit was requested (`backend/app/server.py:170-171`). The document does not mention this.
- The "CLI writes" list is incomplete. `recompute-usage-rollups` flags usage rollups and recomputes trace totals (`backend/app/cli.py:555`). `rollups backfill` writes rollup rows (`backend/app/cli.py:501`). `activate-aggregate-coverage` changes coverage state (`backend/app/cli.py:385`). `demo seed-evals` commits seeded rows (`backend/app/cli_demo.py:34`). `attribution backfill` refreshes daily rollups (`backend/app/cli_attribution.py:91`). `alerts evaluate` evaluates rules unless `--dry-run` is passed (`backend/app/cli_alerts.py:33-61`). None of these appears in the list.
- The import-time side effects leave out tracing. `create_app` calls `configure_tracing(settings, app, engine)`, which installs tracing middleware and a tracer provider at import (`backend/app/main.py:167`). The document names only logging, Sentry, deliverers, engines and the EE license.

## backend/EGM.md

- The alembic one-liner says "Downgrades are refused". Revisions implement real `downgrade()` bodies (for example `DROP TABLE IF EXISTS`, `DROP INDEX CONCURRENTLY`), and tests run them through `backend/app/db/migrations.py:30` (`downgrade_to`). No revision raises on downgrade. The child contract `backend/alembic/EGM.md` also describes downgrade conventions, not refusal.
- The document says the directory "holds configuration only" and lists `pyproject.toml`, `uv.lock`, `alembic.ini`, `Makefile` and `openapi.json`. It leaves out three tracked files a newcomer needs. `backend/.python-version` pins Python 3.13, which must match the Dockerfile's `python:3.13-slim` (a documented gotcha). `backend/.env.example` is the local settings template. `backend/README.md` holds the test-database lifecycle and is mentioned only under `tests`.
- The Verification section's lint step is `uv run mypy app`, but CI type-checks `app load accuracy` (`.github/workflows/ci.yml:47`). The `load` and `accuracy` packages this document describes are therefore checked in CI but not by the command given here.

## frontend/src/features/EGM.md

- The document says pages are the only thing `src/app/router.tsx` takes from features ("lazy-loads every page module"). The router also statically imports a per-feature URL search schema from 15 features (`search.ts`, `sessions-search.ts`, `audit-search.ts`, `budget-prefill.ts`, `price-override-form.ts` and others), plus `auth/queries` (`ensureMe`, `termsPending`), `shell/home-redirect` and `landing/links` (`frontend/src/app/router.tsx:14-40`). These modules land in the entry chunk. The "search schema per feature" convention, and the fact that these files are eagerly loaded, are missing from the Conventions section.

## frontend/src/EGM.md

- The zod invariant is stated only as "`@/lib/zod-config` must stay the first import of `main.tsx`". In the production build the guarantee comes from the `zod` chunk group in `frontend/vite.config.ts`, which bundles `src/lib/zod-config.ts` with zod itself. The module's own comment says that in the entry chunk it ran too late and broke the CSP (`frontend/vite.config.ts:49-52`, `frontend/src/lib/zod-config.ts:4-9`). Moving the import to the top of `main.tsx` alone does not satisfy the invariant, and the document does not mention the chunk group.

## frontend/EGM.md

- The invariant says `scripts/check-public-bundle.mjs` recognises chart code by the `charts` chunk group, so renaming the group "breaks the check". The script also flags any public-route file whose source map includes `node_modules/(recharts|d3-*|victory-vendor)/` (`frontend/scripts/check-public-bundle.mjs:44-48`). A rename only disables the file-name half of the check.
- The bundle check cannot run against the configured build. It reads `dist/.vite/manifest.json` (`frontend/scripts/check-public-bundle.mjs:7`), but `frontend/vite.config.ts` does not set `build.manifest`. No `package.json` script calls the check and neither does `.github/workflows/ci.yml`. This parent document presents it as a check without saying it is manual, needs `vite build --manifest`, and is missing from the Verification section. The child contract `frontend/scripts/EGM.md` does say so.
- CI also runs `node ../scripts/licenses/npm_notices.mjs --check` from `frontend` (`.github/workflows/ci.yml:131`), which fails when a production npm dependency declares no licence. This gate on adding frontend dependencies is not mentioned.

## EGM.md

- The **web** role is described as proxying "`/api`, `/v1`, `/gw` and `/health` to the api". `/gw/*` goes to `{$GATEWAY_UPSTREAM:api:8000}`, which is the standalone gateway when one is configured. The live stream `/api/v1/projects/*/live` has its own unbuffered, uncompressed route. `/metrics` is deliberately not proxied. The upstreams are `API_UPSTREAM`/`GATEWAY_UPSTREAM`, and Caddy caps `/gw` bodies at 11 MiB (`deploy/Caddyfile:51-95`).
- Coverage: Caddy is also the place where Spanlight sets its security headers and three Content-Security-Policy variants (strict for the dashboard, relaxed for `/api/docs`, hash-based for `/docs`) (`deploy/Caddyfile:20-44`). It also chooses which client IP to trust (`trusted_proxies`, `X-Real-IP`, `header_up X-Forwarded-For {client_ip}`; `deploy/Caddyfile:5-9`). The CSP shapes the frontend build (fonts not inlined, the zod jitless chunk), and the client IP drives rate limits and gateway per-key limits. The document mentions neither as a global contract.
- The root inventory lists `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `LICENSE` and `.github/workflows/`. It leaves out the tracked `CODE_OF_CONDUCT.md`, `.dockerignore` and `.github/actions/eval-gate/` (a composite GitHub Action that users consume), as well as the issue and discussion templates, `CODEOWNERS` and `dependabot.yml`.
- The Backend verification command `uv run ruff check && uv run mypy app && ...` leaves out `uv run ruff format --check`. That step is in `backend/EGM.md`, `CLAUDE.md` and CI (`.github/workflows/ci.yml:46`). CI also runs mypy on `app load accuracy` (line 47).
- The Python SDK verification is only `uv run pytest`. CI also runs `ruff check`, `ruff format --check` and `mypy src` (`.github/workflows/ci.yml:83-86`). The TypeScript SDK and docs-site have CI jobs (`.github/workflows/ci.yml:92`, `160-166`) but no verification line in this document.
- The self-host check `deploy/smoke.sh` reads its env file from `SMOKE_ENV_FILE`, which defaults to `.local/compose-test.env` (`deploy/smoke.sh:33-37`), a git-ignored local file. That file must set `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `SECRET_KEY` and a matching http `APP_BASE_URL`/`WEB_PORT`. The document presents the script as runnable as-is. The script also needs docker compose, curl, jq and uv.

## backend/app/reconciliation/EGM.md

- Stale runs: the doc says a `running` run older than `RUN_IN_PROGRESS_WINDOW` is "closed as `timeout`". The code closes it with `status="failed"` and `error="timeout"` (`STALE_RUN_ERROR`); `timeout` is the error text, not a run status (`backend/app/reconciliation/runs.py:139-142`, `backend/app/reconciliation/job_queries.py:95-101`).
- Provider calls off: the doc cites `source_keys.py:check_key` as refusing provider calls while `BILLING_SOURCES_ENABLED` is off. `check_key` does not refuse; it returns `None` and the key is saved unchecked (`backend/app/reconciliation/source_keys.py:67-68`). `ProviderCallsOff` is raised only by the manual refresh check `source_actions.py:check_refresh` (`backend/app/reconciliation/source_actions.py:102-103`), and the scheduled parent job returns `SKIPPED_NOT_CONFIGURED` instead (`backend/app/reconciliation/fetch_jobs.py:72-73`).
- Mapping patterns: the doc says the pattern set is the seed patterns plus the organization's "live" override patterns. `_MAPPING_PATTERNS` selects every `price_overrides.model_pattern` of the org with no `effective_from` filter, so not-yet-effective overrides are included (`backend/app/reconciliation/queries.py:35-42`).
- Retention: the doc says provider lines and fetch runs are kept 400 days. A fetch run older than 400 days is deleted only when no `provider_cost_lines` row still points at it (`backend/app/reconciliation/retention.py:44-46`), so runs referenced by retained lines outlive the 400 days.
- Coverage: `fetch_steps.py` is not mentioned. It holds the fetch child's attempt opening, key decryption and post-fetch rollup refresh (`open_attempt`, `open_key`, `refresh_rollups`, `skip`) and the time budgets `ROLLUP_BUDGET_S` (40 s) and `ROLLUP_HARD_STOP_S` (45 s), which `bypass_alert.py:HARD_STOP_S` reuses (`backend/app/reconciliation/fetch_steps.py:44-45`, `backend/app/reconciliation/bypass_alert.py:55`).
- Coverage: the `queries.py` reads list omits `projects_of_source`, `org_project_ids` and `source_names_taking_projects` (`backend/app/reconciliation/queries.py:85`, `:125`, `:137`), and the fetch-job constant `FETCH_DEADLINE_S` (35 s) is not stated (`backend/app/reconciliation/fetch_jobs.py:67`).

## backend/app/reconciliation/fetchers/EGM.md

- Coverage: `base.py:FetchFailed` is described only by its `status_line`. It also carries `retry` (`"none"`, `"rate_limited"` or `"transient"`) and `retry_after_s`, which `fetch_policy.py:retry_for` and `outcome_label` depend on (`backend/app/reconciliation/fetchers/base.py:57-67`, `backend/app/reconciliation/fetch_policy.py:33-56`). The status-to-retry mapping (429 rate limited, 5xx and 408 transient, every other non-2xx including redirects `none`; timeouts and transport errors transient) is not stated (`backend/app/reconciliation/fetchers/pages.py:94-115`, `:129-141`).
- Coverage: `base.py:FetchResult` is described as "the mapped lines"; it also returns `pages`, the page count of the run (`backend/app/reconciliation/fetchers/base.py:70-72`).
- Coverage: a compressed response (`content-encoding` other than `identity`) is refused as `invalid response`, and a control character or non-ASCII key fails as `key cannot be decrypted` without a request reaching the provider; neither rule is in the contract (`backend/app/reconciliation/fetchers/pages.py:104-111`, `:172-173`).
- The doc says every request goes through the client from `build_http_client`; the package does not build or check the client, it uses whatever `httpx.AsyncClient` the caller passes (`backend/app/reconciliation/fetchers/pages.py:53-60`, `backend/app/reconciliation/fetchers/probe.py:26-40`). The guarantee is the callers', not this package's.

## backend/app/metrics/EGM.md

- Coverage: the aggregate-coverage contract is missing. Every KPI, model row and token-mix response carries `aggregate_status` and `oldest_pending_at` from a `CoverageState` (`backend/app/metrics/service.py:93-94`, `:200`, `backend/app/metrics/token_mix.py:56`). When coverage is unavailable, `overview` and `models` drop the raw/rollup plan, re-read the whole window from rollups and mark it approximate, and a window with no rollup hours returns every KPI field as `null` (`backend/app/metrics/service.py:87-98`, `:176-178`). `timeseries` and `models` also accept a caller-supplied `coverage` argument (`backend/app/metrics/service.py:111`, `:161`).
- The model-list invariant says rollup and raw groups are merged before sorting and cutting. When coverage is unavailable the merged rows are discarded and replaced by a rollup-only read for the whole window (`backend/app/metrics/service.py:176-178`).
- `token_mix` is described as returning tokens by price class; it also returns `fee_usd` and `cost_usd` and widens the window to every UTC day it touches, reporting `first_day` and `last_day` (`backend/app/metrics/token_mix.py:21-29`, `:34-56`). Its reads also go through `read_covered` against the `COST_DAILY` rollup consumers, which the side-effects list omits (`backend/app/metrics/token_mix.py:37-46`).

## backend/app/insights/EGM.md

- Row-level security: the doc says only the cross-project job reads in `job_queries.py`, `jobs.py` and `precision_cli_queries.py` bypass RLS and that they hold only ids and counts. `context_queries.py:_org_provider_errors` also bypasses RLS on every engine run to read every project of the organization (`backend/app/insights/context_queries.py:274-285`), and `job_queries.py:complete_stale_reservations` is a cross-project write, not a read: it bypasses RLS and updates `insight_explanations` rows in every project (`backend/app/insights/job_queries.py:61-83`).
- Coverage: the detector context also loads provider-status incidents (`provider_incidents`, at most 20 per provider with a 5xx, through `app.provider_status`) and a `truncated` flag; the context list in the Invariants omits both (`backend/app/insights/context_queries.py:163-177`, `:294-307`).
- Side effects: an Explain call writes a real span and trace into the project (environment `doctor`, tag `doctor-explain`) through `DirectSpans`; the doc only says it calls Anthropic (`backend/app/insights/explain_service.py:17-19`, `backend/app/insights/explain_settle.py:157-160`, `backend/app/insights/explain_context.py:15-16`). Failed calls the provider may have billed keep a reservation row without text, and the request fails with `502 EXPLAIN_FAILED`; neither is stated (`backend/app/insights/explain_settle.py:1-19`).
- Coverage: the monthly budget is named only as "a monthly organization budget"; the controlling setting is `EXPLAIN_MONTHLY_BUDGET_USD` and the worst case is estimated as prompt characters / 3 input tokens plus `MAX_OUTPUT_TOKENS` (`backend/app/insights/explain_service.py:6-14`).
- Coverage: the quiet-resolve invariant omits that a muted insight whose mute has ended also resolves, and that resolving clears `muted_until` and `mute_reason` (`backend/app/insights/lifecycle_queries.py:155-170`). "Active projects" in the Overview are projects with activity in the last 24 hours, and per-project jobs are staggered by `STAGGER` (5 s) within `STAGGER_SPAN` (10 min); neither is stated (`backend/app/insights/jobs.py:53-57`, `:69-88`).
- Coverage: `digest_queries.py` (`open_counts`, `top_open`, `any_first_seen`), `sensitivity_queries.py:load_levels`, `job_queries.py:active_project_ids` and `open_insights_by_severity` are not listed (`backend/app/insights/digest_queries.py:24-54`, `backend/app/insights/sensitivity_queries.py:17`, `backend/app/insights/job_queries.py:42-50`).

## backend/app/insights/detectors/EGM.md

- Fault attribute name: the doc says spans "with `gateway.fault_scenario` set" are dropped. No such span attribute exists; the stored attribute is `spanlight.fault.scenario`, read into `GatewayAttrs.fault_scenario` / `LlmSpanRow.fault_scenario`, and the cache flag is `spanlight.cache` (`backend/app/insights/context_build.py:35-47`, `backend/app/insights/context_queries.py:139`). The same wrong name is in `base.py`'s docstring (`backend/app/insights/detectors/base.py:10-15`).
- Cache-hit exclusion: the doc lists `retry_storm`, `cache_opportunity` and `unpriced_spend`. `model_retiring` also drops cache hits (and fault spans) (`backend/app/insights/detectors/model_retiring.py:79`), and `client_timeout_misconfigured` excludes cache hits and fault spans from the successful calls it compares aborts with (`backend/app/insights/detectors/client_timeout_misconfigured.py:45-47`), although the doc says client-behaviour detectors keep fault spans.
- Fault exclusion for `provider_incident` is not done by the detector: the org-wide counts arrive pre-filtered by the engine's SQL, and only the project's own evidence spans are filtered in the detector (`backend/app/insights/context_queries.py:127-143`, `backend/app/insights/detectors/provider_incident.py:76-86`). `model_retiring` also drops fault spans but is not in the list of traffic and provider-health detectors that do (`backend/app/insights/detectors/model_retiring.py:79`).
- Coverage: `baselines.py:group_spans` is not listed among the shared helpers (`backend/app/insights/detectors/baselines.py:53`).

## backend/app/releases/EGM.md

- Coverage: the window cap is stated, but not its error: a window longer than 30 days is refused with 422 `RELEASE_WINDOW_TOO_LARGE` by all three service functions (`backend/app/releases/service.py:57-61`). The unseen-release 404 carries the code `UNKNOWN_RELEASE`, which the doc does not name (`backend/app/releases/service.py:94-97`).
- `failure_category_rates` also returns 404 when a `failure_category` config exists but is not categorical; the doc only covers a missing config (`backend/app/releases/service.py:133-134`).
- Side effects: `failure_category_rates` also reads the project's score config through `app.scores` (`failure_category_config`), which the read-only table list (`traces`, `spans`, `scores`) omits (`backend/app/releases/service.py:132`).

## backend/app/evals/EGM.md

- Reapers: the doc says running runs with a heartbeat older than `ABANDONED_AFTER` "are failed `abandoned` and charged their reservation", citing both `reservation.py:reap_abandoned_runs` and `experiment_queries.py:reap_abandoned_runs`. Only the evaluator reaper charges (`cost_usd = reserved_usd`) (`backend/app/evals/reservation.py:204-212`); the experiment reaper sets `failed`/`abandoned` with no charge (`backend/app/evals/experiment_queries.py:378-382`). Both reapers also fail runs that are not stale by heartbeat: the evaluator reaper fails queued online runs whose `run_evaluator` job is gone, and the experiment reaper fails any queued or running run whose `run_experiment` job is gone (`backend/app/evals/reservation.py:198-203`, `backend/app/evals/experiment_queries.py:368-374`).
- Coverage: the sampler pass (`jobs.py:sample_all`) also reaps abandoned playground replays (`app.playground.replay_queries.reap_abandoned_replays`) and records `REPLAY_RUNS` and `REPLAY_COST`; the reaper list and the metrics list omit this (`backend/app/evals/jobs.py:134`, `:144-148`). Reaping always runs, even with `EVALS_ENABLED` off; only queuing is gated (`backend/app/evals/jobs.py:131-135`).
- Coverage: the per-cycle cap `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` (`settings.eval_sampler_max_runs_per_cycle`), the sampler's 40 s budget (`RUN_BUDGET_SECONDS`), its `SAMPLE_WINDOW` (5 min) and `SETTLE_LAG` (30 s), and the `skipped_cap` / `skipped_no_payload` metric outcomes are not in the contract (`backend/app/evals/jobs.py:72-79`, `:149`, `:228-231`).
- The `EXPERIMENT_SCHEDULES_ENABLED` gate is cited to `experiment_schedule.py`; the check is in the scheduler, which only schedules `schedule_experiments` when the flag is on (`backend/app/jobs/scheduler.py:180`). `run_schedule_experiments` itself does not check it (`backend/app/evals/experiment_schedule.py:51-75`). `schedule_experiments` has 3 attempts, unlike the one-attempt runs (`backend/app/jobs/scheduler.py:128-130`).
- Row-level security: the doc names `sampling_evaluators` and `scheduled_experiments` as the only cross-project reads. `sample_all` bypasses RLS for the whole reap-and-list transaction, including the three reaper writes (`backend/app/evals/jobs.py:130-136`).
- Coverage: `experiment_item_row.py` (`storable_row`, `copy_of`, `json_score`, `MAX_ERROR_CHARS` 500) is not mentioned (`backend/app/evals/experiment_item_row.py:13-38`).

## backend/app/evals/evaluators/EGM.md

- Judge failure codes: the doc cites `judge.py:_failure_code` for `verdict_unparseable`. `_failure_code` only returns `judge_status_<code>` or `judge_no_answer`; `verdict_unparseable` and `judge_truncated` are raised by the caller in `JudgeEvaluator` (`backend/app/evals/evaluators/judge.py:350-356`, `:230`). `judge_status_<code>`, `judge_no_answer` and `judge_truncated` are not listed anywhere in the contract.
- Webhook coverage: the request also carries an `x-spanlight-timestamp` header that the signature covers, and a `version` field (`PAYLOAD_VERSION` `2026-10-01`) (`backend/app/evals/evaluators/webhook.py:40`, `:101-109`, `:177-181`). The `metadata` field has its own cap, `MAX_METADATA_BYTES` (16 KB), and a body still over 256 KB after capping fails with `webhook_payload_too_large`, a fourth failure code the stable-code list omits (`backend/app/evals/evaluators/webhook.py:43`, `:91`, `:110-111`).
- Webhook egress: the doc points at the egress checks in `app.core.egress`. The client is built by `app.alerts.channels.http.build_alert_client(allow_private=False)`, which is what applies them (`backend/app/evals/evaluators/webhook.py:28`, `:167`).

## backend/app/core/EGM.md

- Origin check: the doc says a state-changing `/api` request without an allowlisted Origin is rejected, with `/api/v1/email/unsubscribe` as the only exception. Requests carrying `Authorization: Bearer` are also exempt from the Origin check (`backend/app/core/middleware.py:159-163`, `:176-181`). The refusal is 403 `ORIGIN_NOT_ALLOWED`, which the doc does not name (`backend/app/core/middleware.py:184-186`).
- Egress: the doc says outbound connections to user-chosen URLs "only reach public addresses". `egress.py` has an `allow_private` mode that opens the private ranges (only link-local and cloud metadata stay blocked), switched on for webhook and alert targets by `WEBHOOK_ALLOW_PRIVATE_TARGETS` (`backend/app/core/egress.py:114-119`, `:195-197`, `:285-286`, `backend/app/config.py:221`).
- Coverage: the body-limit invariant does not give the upload cap or the status: the two upload routes allow `UPLOAD_BODY_BYTES` (5 MiB plus 64 KiB of multipart framing) and an oversize body is refused with 413 (`backend/app/core/body_limit.py:27-37`, `:44-45`).
- Coverage: idempotency's in-flight window `IN_FLIGHT_TIMEOUT` (60 s), after which an unfinished key can be taken over, is not stated (`backend/app/core/idempotency_store.py:35`). The admission 503 also carries `Retry-After: 1` (`backend/app/core/admission.py:7`).

## backend/app/playground/EGM.md

- Coverage, replay body rules: besides the listed rewrites, `build_replay` turns `service_tier: "auto"` into the surface's standard tier (`standard_only` for Messages, `default` otherwise) (`backend/app/playground/replay_request.py:85-89`). When a body has no output cap it adds one (the Anthropic default 1024 on `messages`, `MAX_OUTPUT_TOKENS` on the OpenAI surfaces), so the clamp rule `min(stored, override, MAX_OUTPUT_TOKENS)` is not the only case (`backend/app/playground/replay_request.py:115-131`). A credential of another provider family is refused with `REPLAY_PROVIDER_MISMATCH` (`backend/app/playground/replay_request.py:152-153`).
- Coverage: "options the budget cannot price" are not named: a non-standard `service_tier`, chat `web_search_options`, and tools not billed by tokens alone (hosted shell, server tools), refused as `REPLAY_UNPRICED_OPTION` (`backend/app/playground/replay_request.py:92-112`, `:170-172`).
- Coverage: replay run error codes `replay_failed` and `gateway_budget_blocked` set by the settle step are not listed (`backend/app/playground/replay_settle.py:42-43`).

## backend/app/auth/EGM.md

- Re-authentication: the doc describes the proof as a password, or a recent sign-in for a password-less account, failing with `403 REAUTH_REQUIRED` or `422 INVALID_PASSWORD`. When the account has two-factor authentication on, `confirm_account_owner` also requires a TOTP or recovery code and fails with `422 TOTP_CODE_REQUIRED` or `422 INVALID_TOTP_CODE`; past the attempt limit it answers 429; without `CREDENTIALS_KEYS` it answers `NOT_CONFIGURED`. Each refusal is committed before raising so it stays counted, and the throttle counter is per `scope` (`backend/app/auth/reauth.py:38-85`).
- Side effects: the doc says the package writes `login_attempts`. It only reads them; the rows are written by `api/v1/auth.py` and `api/v1/totp.py` (`backend/app/auth/login_throttle.py:47-58`, `backend/app/api/v1/auth.py:245`, `backend/app/api/v1/totp.py:270`). The package does write `throttle_events` (hash budget, forgot-password limits and the re-auth counter through `app.core.throttle`), which the side-effects list omits (`backend/app/auth/login_throttle.py:74-76`, `backend/app/auth/reauth.py:52`).
- Password reset coverage: a successful reset also marks an unverified email as verified and replaces the password through `app.services.credentials.replace_password`, which ends every session (`backend/app/auth/password_reset.py:96-101`). The audit event goes to every organization of the user (`record_user_audit_in_each_org`) (`backend/app/auth/password_reset.py:103-108`).
- `tokens.py:revoke_token` is described as idempotent; it returns a distinct `RevokeOutcome` (`REVOKED`, `ALREADY_REVOKED`, `NOT_FOUND`) that callers can tell apart (`backend/app/auth/tokens.py:44-48`, `:118-125`).
- Coverage: the token TTLs are not stated: verification links 24 h (`VERIFY_TOKEN_TTL`), reset links 1 h (`RESET_TOKEN_TTL`) (`backend/app/auth/verification.py:13`, `backend/app/auth/password_reset.py:22`). The `spl_oauth` cookie path `/api/v1/auth/oauth` and `MAX_NEXT_LENGTH` (2048) are also not stated (`backend/app/auth/oauth_state.py:32`, `:38`).

## backend/app/ee/EGM.md

- Import boundary: the doc says core code never imports `app.ee` and only composition roots do, "through `hooks.py`", enforced by TID251. `app/config.py` and `app/cli.py` import `app.ee.license.parse_license` (and `LicenseError`) directly, not through `hooks.py` (`backend/app/config.py:13`, `backend/app/cli.py:67`), and the worker imports `app.ee.hooks` with an explicit `noqa: TID251` (`backend/app/jobs/worker_lifecycle.py:13`). The Overview and the Invariants contradict the Interface line that names `config.py` and `cli.py` as callers.
- `hooks.py:load` is described as called from `main.py`; the worker also calls it for its log line (`backend/app/jobs/worker_lifecycle.py:13`, `backend/app/ee/hooks.py:1-5`).
- "Feature names a build does not know are ignored": `License.features` keeps every name from the key, unknown ones included, and logs them at startup; they are ignored only in the sense that no `EEFeature` value can ask for them (`backend/app/ee/license.py:57-70`, `:161-165`, `backend/app/ee/hooks.py:46`).
- Coverage: other `LicenseError` causes (missing `spl_lic_` prefix, non-base64url segments, non-object JSON payload, missing `license_id`/`licensee`, times without a time zone, non-list `features`) are not listed alongside the length and version checks (`backend/app/ee/license.py:82-88`, `:123-165`).

## backend/app/provider_status/EGM.md

- Polling switch: the doc says the job polls every feed every 5 minutes and "always ends `done`". Polling is controlled by `PROVIDER_STATUS_POLLING_ENABLED` (default on): the scheduler only schedules the job when it is on, and a queued run with it off returns `SKIPPED_NOT_CONFIGURED` without fetching (`backend/app/jobs/scheduler.py:176`, `backend/app/provider_status/jobs.py:30-33`, `backend/app/config.py:237`). The flag is not mentioned.
- Fallback writes: the doc says incidents are upserted on `(provider, external_id)` without the fallback rules. An upsert from the fallback feed keeps the primary's `source`, `impact` and `started_at`, and keeps `status` and `resolved_at` when the fallback status is `unknown`; a `started_at` after `resolved_at` is clamped to `resolved_at` (`backend/app/provider_status/service.py:27-47`, `:66-74`, `:101-104`).
- Validators: the doc says `fetched` stores the response's `etag` and `last_modified`. A successful fallback poll records `fetched` with both validators cleared to `NULL`, so the next primary request is unconditional (`backend/app/provider_status/poller.py:89-94`).
- Coverage: a database error while storing a parsed feed is rolled back and recorded as `parse_error` (`backend/app/provider_status/poller.py:150-155`); `record_poll` also maintains `last_success_at`, `consecutive_failures` and `last_error_type` (`backend/app/provider_status/service.py:136-143`). `queries.py:MAX_OVERLAPPING` (20 incidents per provider for `overlapping_api_incidents`) is not stated (`backend/app/provider_status/queries.py:24`).

## backend/app/provider_status/adapters/EGM.md

- Coverage: how each adapter decides `api_affected` is missing, though `overlapping_api_incidents` and the `provider_incident` detector depend on it. Anthropic: any component name containing `API` (case-sensitive); OpenAI: `API_PATTERN` over the title and newest update; Google: always `True` for kept incidents (`backend/app/provider_status/adapters/anthropic.py:17-19`, `backend/app/provider_status/adapters/openai.py:16-30`, `backend/app/provider_status/adapters/google.py:69`).
- Status and impact normalisation: the doc says unknown words become `unknown` through `fields.py:normal_status` and `normal_impact`. The Google adapter does not use them: impact comes from its own `IMPACTS` map of `status_impact` codes, and status is derived only from whether `end` is set (`investigating` or `resolved`), so it is never `unknown` (`backend/app/provider_status/adapters/google.py:28-32`, `:67-68`).
- Coverage: Google incidents that name no `PRODUCT_MARKERS` product are dropped silently, not counted as skipped, and a Google incident without `begin` refuses the whole feed (`backend/app/provider_status/adapters/google.py:59-65`, `backend/app/provider_status/adapters/fields.py:167-191`).
- `MAX_INCIDENTS_PER_FEED` counts kept incidents only, after per-incident skips and silent drops, not items in the feed (`backend/app/provider_status/adapters/fields.py:187-190`). The RSS `DESCRIPTION_LIMIT` is a truncation before parsing, not a cap on stored text (`backend/app/provider_status/adapters/openai_rss.py:89`).
- Coverage: `types.py:FeedAdapter` also requires `provider`, `source`, `url_prefix`, `fallback_source`, `parse_fallback` and `parse_fallback_with_report`; the doc lists only `url` and `fallback_url` (`backend/app/provider_status/adapters/types.py:53-80`).

## backend/app/providers/EGM.md

- Retries: the doc says the report "flags untracked traces". It does more: when any untracked trace (stored before retries were tracked) started in the window, or aggregate coverage is unavailable, `RetryReport.for_provider` returns unknown (`None`) retries and cost for every provider, not just a flag (`backend/app/providers/retry_queries.py:103-111`, `:149`). A provider with no retrying traces gets a known `0`, not `None` (`backend/app/providers/retry_queries.py:106-110`).
- Coverage: the aggregate-coverage contract is missing. `health` and `timeseries` read through a `CoverageState`, return `aggregate_status`/`oldest_pending_at`, and when coverage is unavailable `health` re-plans the whole window onto rollups and `timeseries` may return `by_class: null` (`backend/app/providers/service.py:64-69`, `:90`, `:200-202`).
- Coverage: sample rows read the retry-after from `spanlight.gateway.retry_after_s`, falling back to `provider.retry_after_s`, and drop a `provider.request_id` longer than 128 characters (`_MAX_REQUEST_ID`); the bounds invariant names only retry-after and attempts (`backend/app/providers/service.py:52`, `:236-243`, `backend/app/providers/sample_queries.py:79-83`). Attempts are capped at 4 digits (`_MAX_ATTEMPTS_DIGITS`) (`backend/app/providers/service.py:51`, `:275-279`).
- Coverage: `health` also returns a fixed `retry_cost_note` (`RETRY_COST_NOTE`) and `approximate` when any rollup hour was read (`backend/app/providers/service.py:42`, `:85-91`).

## backend/app/ingest/EGM.md

- Ingest caps scope: the Overview says per-project caps "are enforced here" and that the gateway recorder and demo job write through the same pipeline. The caps are checked in `app.api.ingest._store` for the two span ingest routes only; `ingest_spans` does not check them, so gateway-recorded and demo spans are neither refused nor counted against the quotas (`backend/app/ingest/quota.py:1-7`, `backend/app/api/ingest.py:209`, `backend/app/gateway/span_write.py:56`, `backend/app/jobs/tasks/demo_traffic.py:134`).
- Cap semantics missing: a batch is refused only when today's usage is already at or over the limit (the batch that crosses the limit is admitted), daily quotas are checked before the rate bucket, and a replay of an already stored batch is admitted at the cap. Refusals carry code `RATE_LIMITED` or `INGEST_QUOTA_EXCEEDED` (`backend/app/ingest/quota.py:9-16`, `:87-101`, `:345-372`).
- Side effects: a refused request counts its spans in the process loss counter (`quota_rate`, `quota_spans`, `quota_bytes`), which is flushed to `telemetry_losses_hourly`; metrics `INGEST_QUOTA_REJECTIONS`, `INGEST_QUOTA_REJECTED_SPANS`, `INGEST_QUOTA_UTILISATION` and `RATE_LIMIT_REJECTIONS` are recorded (`backend/app/ingest/quota.py:13-15`, `:32-37`, `:352`, `:364-365`). The doc names only "ingest quota counters".
- Coverage: the gateway also reaches the pipeline through `gateway/tracing.py` (`recorder.record`), not only `gateway/span_write.py` (`backend/app/gateway/tracing.py:4`). The cost ceiling `MAX_STORED_COST` (1,000,000), `MAX_TOKEN_COUNT` (2,000,000,000) and `MAX_WEB_SEARCH_REQUESTS` (100,000) bounds are not stated (`backend/app/ingest/normalize.py:37`, `backend/app/ingest/schemas.py:34-35`). `MAX_PROTOBUF_MESSAGES` is 300,000 (`backend/app/ingest/otlp.py:112`).
- Duplicates: within a batch the later duplicate wins, and the earlier occurrence is reported in `rejected` with the reason "duplicate span_id in batch"; the doc only says the last wins (`backend/app/ingest/pipeline.py:165-185`).

## backend/app/ingest/conventions/EGM.md

- Coverage: `map_fields` has an OpenInference rule the doc omits: an `LLM` span with only `input.value`, no flattened messages and no usage (a LlamaIndex prompt-building step) is stored as kind `chain` with no model (`backend/app/ingest/conventions/__init__.py:62-63`, `:93-106`). With no convention key, `map_fields` returns only an inferred kind (`infer_kind`, using `client_span`) (`backend/app/ingest/conventions/__init__.py:55-57`).
- `fill_gaps` invariant is incomplete: the span kind is always the primary mapper's, and dropped-message counts are summed (`backend/app/ingest/conventions/__init__.py:31`, `:67-90`).
- Coverage: `detect`'s priority order and markers are not stated: `openinference.span.kind`, then Vercel (`ai.operationId`, or any `ai.*` key with `ai.model.id`), then `traceloop.span.kind`, then any `gen_ai.*` key (`backend/app/ingest/conventions/__init__.py:34-46`).
- `read.py` also re-exports `as_text` from `app.ingest.otlp_usage`, which `ingest/otlp_span.py` imports from here; the doc says only `first` is used by `otlp_span.py` (`backend/app/ingest/conventions/read.py:11-13`, `backend/app/ingest/otlp_span.py:17`).

## backend/app/end_users/EGM.md

- Side effects: `request_erasure` first deletes a finished (`done` or `failed`) `erase_end_user` job with the same dedupe key from `jobs`, so a later request runs again and the old payload's raw user id is dropped (`backend/app/end_users/erasure_job.py:64`, `:92-103`). The doc lists only an update of the job row, and its "second request is a no-op" invariant holds only while the first is queued or running.
- Refresh jobs are switched by `USER_STATS_ENABLED`: the scheduler schedules `refresh_user_stats` and `refresh_user_stats_deep` only when it is on (`backend/app/jobs/scheduler.py:169-171`). The flag is not mentioned. The nightly pass is runnable from 02:00 UTC (`backend/app/jobs/scheduler.py:66-71`).
- The 90-day window cap is cited to `days.py:day_bounds` and `service.py`; the service docstring says it is the shared API window limit, not a check in this package (`backend/app/end_users/service.py:5`, `backend/app/end_users/days.py:48-55`).
- Erasure also clears `evaluator_runs.error`, `insight_explanations.text` for insights citing the user's traces, and the `user_id` filter of `exports` (`backend/app/end_users/erasure_job.py:210-252`); the doc says "updates" these tables without saying which columns.

## backend/app/datasets/EGM.md

- Redaction scope: the doc says items copied from traces follow the project's `redact_contact_info` setting. Every item write applies it: `add_items` redacts uploaded and API-added items too, and `update_item` redacts `expected_output` and `metadata` (`backend/app/datasets/service.py:152-154`, `:213-217`).
- Coverage: `add_from_traces` also rejects a trace and span already in the dataset with `already in this dataset`, de-duplicates the requested trace ids, and returns `also_in` naming the project's other datasets holding each added trace (`backend/app/datasets/service.py:171-199`, `backend/app/datasets/queries.py:158-191`).
- Coverage: `update_dataset` only changes the description and records no audit event; `update_item` can change only `expected_output` and `metadata`, not `input` (`backend/app/datasets/service.py:90-104`, `:202-219`).

## backend/app/scores/EGM.md

- Side effects: the write path also increments the Prometheus counter `SCORES_WRITTEN`; the doc lists only log lines (`backend/app/scores/service.py:37`). Deleting a score writes a log line only, no audit event, which the doc does not say (`backend/app/scores/service.py:175-196`).
- Coverage: editing a config through `update_config` turns an implicit config explicit (`implicit = False`) (`backend/app/scores/configs.py:86-88`). The `has_scores` repair only looks at scores from the last `REPAIR_WINDOW` (15 min), at most `REPAIR_LIMIT` (5000) traces per sweep, and is run by the `rollup_hourly` job (`backend/app/scores/repair.py:23-25`, `backend/app/scores/service.py:19-20`).
- Coverage: `queries.py` limits `MAX_TRACE_SCORES` (1000) and `MAX_SUMMARY_SCORES` (10), and `schemas.py:MAX_CATEGORIES` (100) are not stated (`backend/app/scores/queries.py:22-25`, `backend/app/scores/schemas.py:34`).

## backend/app/explorer/EGM.md

- Coverage: the error codes callers see are not named: a search that hits `SEARCH_TIMEOUT_MS` is answered `422 SEARCH_TOO_BROAD` (`search_too_broad`), and a trace over the payload budget for a caller without `span_content=bounded` is `422 SPAN_CONTENT_TOO_LARGE` (`backend/app/explorer/content_search.py:36-39`, `:78`, `backend/app/explorer/trace_queries.py:48-57`).
- Coverage: the payload budget applies to `trace_spans` only; `trace_span` returns one span with all its content whatever the trace's size (`backend/app/explorer/trace_queries.py:205-217`). The budget counts `payload_bytes` (or `LEGACY_PAYLOAD_BYTES` when unknown) plus the attributes' size, and withheld spans lose their attributes as well as input and output (`backend/app/explorer/trace_queries.py:73-76`, `:166-189`).
- Coverage: `trace_histogram` uses hour buckets for windows of 48 hours or less (`HOURLY_UP_TO`) and day buckets otherwise, leaves out empty buckets, and counts as errors the traces with at least one failed span (`backend/app/explorer/trace_histogram.py:15`, `:32-44`). The sort keys (`TraceSort`: `started_at`, `duration`, `cost`, `tokens`, `retry_cost_usd`; `SessionSort`: `last`, `cost`, `turns`, `errors`, `tokens`) are not listed (`backend/app/explorer/trace_sort.py:34-35`, `backend/app/explorer/session_queries.py:52`).

## backend/app/storage/EGM.md

- Backups do not use `get_object_store`: `backups/storage.py` builds its own `S3ObjectStore` from the separate `BACKUP_S3_*` settings, calling the private `s3.py:_build_client`, and passes `sse=settings.backup_s3_sse` (default `AES256`) (`backend/app/backups/storage.py:15-33`, `backend/app/config.py:91-99`). The doc presents `get_object_store` as the store for "backups and exports" and does not mention the second configuration or that `_build_client` is used outside the package.
- Coverage: `S3ObjectStore` accepts an `sse` argument that adds `ServerSideEncryption` to every new object, but `from_settings` never sets it, so export objects are written without it (`backend/app/storage/s3.py:79-91`, `:93-117`). Presigned links are signed against `S3_PUBLIC_ENDPOINT` when it differs from `S3_ENDPOINT`, and always carry `Content-Disposition: attachment` (`backend/app/storage/s3.py:106-115`, `:187-202`).
- Coverage: `put` of a `bytes` body larger than `PART_SIZE` also goes multipart, not only streamed uploads, and a failed multipart abort is logged (`object_store.abort_multipart_failed`) rather than raised (`backend/app/storage/s3.py:124-136`, `:265-272`). The client uses 3 retries, a 10 s connect and 60 s read timeout, and region `us-east-1` by default (`backend/app/storage/s3.py:33`, `:48-58`).
- Callers list: `exports/jobs.py`, `exports/service.py`, `exports/purge.py` and `jobs/tasks/cleanup.py` also use the store and are not listed (grep of `get_object_store` / `app.storage` imports under `backend/app`).

## backend/app/exports/EGM.md

- Trace cap: the doc says more than `MAX_EXPORT_TRACES` traces "arriving while streaming" fails the export. The job also counts matching traces before writing anything and fails `EXPORT_TOO_LARGE` up front when there are more; attribution exports have no up-front count and stop at the row cap while writing (`backend/app/exports/jobs.py:196-203`).
- Coverage: the in-progress limit answers `409 TOO_MANY_EXPORTS`, an attribution request is validated (422) before anything is queued, and the `create_export` job has `EXPORT_JOB_ATTEMPTS` (3) attempts, with only the last attempt recording the failure (`backend/app/exports/service.py:35`, `:79-109`, `:121-127`, `backend/app/exports/jobs.py:137-141`). A statement timeout is not retried (`backend/app/exports/jobs.py:131-135`).
- Coverage: without object storage the export job marks the export failed `NOT_CONFIGURED` and returns `SKIPPED_NOT_CONFIGURED`, and the purge job returns `SKIPPED_NOT_CONFIGURED` without deleting anything (`backend/app/exports/jobs.py:110-114`, `backend/app/exports/purge.py:40-44`). The purge deletes everything under `exports/<project_id>/` (`backend/app/exports/purge.py:26-28`).
- Coverage: the formula-neutralising prefixes are `=`, `+`, `-`, `@`, tab and carriage return (`backend/app/exports/writers.py:44-49`).


## backend/app/alerts/EGM.md

- Invariants say "every `rules_queries.py` read excludes `kind = budget`". Only the rule reads do (`_people_rules`, `lock_rule`, `count_rules`, backend/app/alerts/rules_queries.py:21-77). `list_events`, `get_event` and `lock_event` deliberately include budget rules' events (backend/app/alerts/rules_queries.py:93-153; docstring at :111 says "budget rules' included"), and `get_state` / `org_channel_ids` have no kind filter at all.
- The Overview's list of callers is incomplete. `app/api/v1/budgets.py` imports `rule_errors.UnknownChannelError` (backend/app/api/v1/budgets.py:19). `app/api/v1/audit_streams.py` reuses `channel_test.CHANNEL_TEST_LIMITER` for audit-stream test sends (backend/app/api/v1/audit_streams.py:13,301), so the "10 tests per channel per hour" bucket is not alert-only. `app/reconciliation/bypass_alert.py` reuses `digest_queries.digest_already_queued` / `lock_digest`, `formatting.format_money` and `subject.subject_safe` (backend/app/reconciliation/bypass_alert.py:32-34). `metrics.plan_window` / `rollup_split` are also imported by `providers/series.py` and `providers/queries.py`, not only the three modules named.
- `service.py:create_channel` is described only as a validating insert. It also generates the webhook signing secret server-side (`_first_secret`, backend/app/alerts/service.py:97-106) and returns it once as the second tuple element. `update_channel` supports `rotate_secret` (webhook only, else `field_problem`) and returns the new secret only on rotation (backend/app/alerts/service.py:207-214, :194). Neither the return contract nor rotation is in the doc.
- `digest_job.py:run_weekly_digest` is documented only as "queue each eligible project's weekly digest". It returns `JobOutcome.SKIPPED_NOT_CONFIGURED` when `WEEKLY_DIGEST_ENABLED` is false or no email provider is configured, and it raises `WeeklyDigestIncompleteError` when any project failed or was not reached, so the job retries (backend/app/alerts/digest_job.py:72-105). `digest_queries.digest_recipients` also always excludes the shared demo user (backend/app/alerts/digest_queries.py:85-104). None of this is in the contract.
- Side Effects say `delivery_fanout.py:enqueue_for_channel` inserts outbox rows. It omits that a failed email render still enqueues the rows already marked failed (`EMAIL_RENDER_FAILED`) and increments `spanlight_notifications_delivered_total{outcome="failed"}` per recipient at enqueue time (backend/app/alerts/delivery_fanout.py:119-141).
- `channel_test.py:send_test` also completes an idempotency record (`app.core.idempotency_store.complete_operation`) and sets `verified_at` only if the channel's `updated_at` is unchanged since queueing (backend/app/alerts/channel_test.py:83-108). The doc mentions neither the idempotency side effect nor the conditional verification.

## backend/app/alerts/channels/EGM.md

- Invariants say outbox targets "carry only `channel_id` (plus `delivery_id` for webhooks)". The stored target is `{"channel_id"}` for every Slack, webhook and PagerDuty row (backend/app/alerts/delivery_fanout.py:142-149). `delivery_id` is never stored. The dispatcher adds it to every target of every kind at delivery time (`row.target | {"delivery_id": ...}`, backend/app/notifications/delivery.py:147). `WebhookDeliverer.deliver` then requires it and raises `PermanentDeliveryError(BAD_TARGET)` without it (backend/app/alerts/channels/webhook.py:66-68).
- Interface says `WebhookDeliverer.deliver` "POSTs canonical JSON". That holds only for alert and insight payloads. A channel test payload is sent as `json.dumps(to_json_dict())` with compact separators, not `canonical_json()` (backend/app/alerts/channels/webhook.py:39-49).
- Invariants say "`_header` clips to Slack limits (header 150, section 3000, field 2000)". `_header` clips only to `HEADER_LIMIT` (150) (backend/app/alerts/channels/slack_format.py:75-77). The section and field limits (`SECTION_LIMIT`, `FIELD_LIMIT`, :25-26) are applied by other helpers through `_escaped_clip`.
- Not documented: `target.py:summary_event` and the shared `BAD_TARGET` / `BAD_PAYLOAD` messages (backend/app/alerts/channels/target.py:20-21, 49-54), which webhook and PagerDuty use for logging and errors. The Slack deliverer duplicates its own copies (backend/app/alerts/channels/slack.py:27-32) and does not use `resolve_target`.

## backend/app/annotations/EGM.md

- Invariants say "`service.py:_check_spans` accepts a `span_id` only if it is a stored span of the item's trace". For a span-scoped item (an item with its own `span_id`), any other `span_id` is refused even when that span belongs to the trace. Only the item's own span, or no span, is accepted (backend/app/annotations/service.py:268-284).
- `queries.py:list_items` also filters by `reason` (`ReviewReason`) as well as `status` (backend/app/annotations/queries.py:152-182). Items carry a `reason` column that the review loop sets, and `QueueProgress.by_reason` groups on it. The doc mentions per-reason progress but not the `reason` filter, and does not say which writer sets `reason`.

## backend/app/rollups/EGM.md

- Invariants say `recent_queries.py:settle_recent` "examines at most `CHUNK` (3 h) of closed hours". That bound applies only to the closed-hour cursor range. With the default `recent=True` it also recomputes the moving window `floor_hour(now) - 2h` to `floor_hour(now) + 1h`, which covers open, unclosed hours, and merges it with the cursor range when they overlap (backend/app/rollups/recent_queries.py:46-55, 84).
- `recent_jobs.py:run_recent` is documented as updating `aggregate_job_progress` and running `repair_has_scores`. It also runs a maintenance round per visited project through `maintenance.py:maintain_project`. That round either refreshes one due 6-hour slice from the last 48 hours (`refresh.py:due_slices`, `SLICE`, `REFRESH_AGE`) or fills one historical gap of up to 24 h (`refresh.py:_find_gaps`) (backend/app/rollups/recent_jobs.py:89-125, 155-175; backend/app/rollups/maintenance.py:13-36; backend/app/rollups/refresh.py:16-19). Neither `maintenance.py` nor the slice/gap logic in `refresh.py` is in the contract. `refresh.py` is mentioned only for `MAX_BACKFILL_DAYS`.
- `watermark.py:read_split` is described as "capped by the closed-hour cursor and `RAW_TAIL_MAX`". The cursor is an upper cap: `min(floor_hour(computed_at - ROLLUP_GRACE), next_closed_hour)`. `RAW_TAIL_MAX` is a lower floor: `max(frontier, floor_hour(now - 24h))`, so rollups are read even when they are stale. The function also returns `None` for a project with no rollup rows and raises `RuntimeError` when the `rollup_recent` progress row is missing (backend/app/rollups/watermark.py:31-43). The contract states none of these.
- The Overview's consumer list (metrics, providers, alerts, attribution, end-users, reconciliation, insights, ingest) omits `app/gateway/span_losses.py`, `app/releases/service.py`, `app/telemetry_losses`, `app/api/health.py` and `app/jobs/tasks/retention.py`. The retention task is the caller of `retention.py:delete_hourly_rollups` and of `coverage.py:mark_trace_source_loss(..., "retention")` (backend/app/jobs/tasks/retention.py:70-71, 244, 277). `app/end_users/erasure_job.py:295` is the `"erasure"` caller. The doc does not name the callers of these side-effecting helpers.
- `compute.py` defines `_KEY_SHARE_PROJECT` (backend/app/rollups/compute.py:96-103) with a long lock-order comment, but `compute_rollups` never uses it. The `FOR KEY SHARE` lock is taken inside `coverage.py:lock_aggregate_sources` (backend/app/rollups/coverage.py:17-30), which also calls `bind_project`. The doc's lock-order claim matches the behaviour, but the constant is dead code that suggests a second lock site.

## backend/app/review/EGM.md

- Invariants attribute the outlier rule to "`outliers.py:outlier_threshold` uses `percentile_disc(0.99)` over at least `OUTLIER_MIN_SAMPLE` (30)". Production selection never calls `outlier_threshold`, `is_outlier` or `percentile_disc`. Nothing in `app/` or `tests/` references them, and only the constant `OUTLIER_MIN_SAMPLE` is imported (backend/app/review/selection_queries.py:30). The threshold is actually computed in SQL by `selection_sql.py:OUTLIER` (`percentile_disc(0.99) WITHIN GROUP`, `HAVING count >= :min_sample`, backend/app/review/selection_sql.py:88-111). The Python functions in backend/app/review/outliers.py:17-35 are an unused mirror.
- Not documented: every candidate statement also excludes traces that already have any item in the target queue, whatever its `span_id` (`_NOT_QUEUED`, backend/app/review/selection_sql.py:37-42). `selection.py` then adds the day's already-queued traces (`ALREADY_QUEUED`, :125-133) to `skipped_duplicates` (backend/app/review/selection.py, `already_queued` call). The doc names only the `INSERT_ITEMS` `ON CONFLICT` as the dedupe.
- `jobs.py:run_select_for_review` is described as running "for the UTC day that just ended". The contract omits that the nightly job is scheduled only when `settings.review_selection_enabled` (`REVIEW_SELECTION_ENABLED`) is on (backend/app/jobs/scheduler.py:178-179), at 01:00 UTC with 3 attempts (:109-110).
- `reasons.py:DETAIL_KEYS` is called "the contract for each reason's `reason_detail` keys". Every builder also writes an `"also"` key (backend/app/review/reasons.py:26-58), which `select._one_per_trace` fills (backend/app/review/select.py). `"also"` is not in `DETAIL_KEYS`.
- `queries.py:latest_selection`, `selection_for_day` and `job_id_for` are omitted from the interface. `latest_selection` feeds `last_selection` in `GET /review-policy` (backend/app/api/v1/review_policy.py:61). `request_run` uses `selection_for_day` to raise `AlreadySelectedError` and `job_id_for` to return the job id (backend/app/review/service.py, `request_run`).

## backend/app/live/EGM.md

- Interface says `markers.py:read_markers` "returns a `(row_count, generation_sum)` marker per project". A project with no dirty-hour or dirty-shard rows is absent from the result (backend/app/live/markers.py:23-31). `hub.py` substitutes `NO_MARKER = (0, Decimal(0))` for such projects in `_poll` and `prime` (backend/app/live/hub.py:28, 154, 195). As a result, `LiveHub.marker` returns `(0, 0)`, not `None`, for a watched project with no changes, and `cached_answer` still caches it. The contract implies `None` and a cache skip.
- `cache.py:AnswerCache.get_or_compute` also increments `spanlight_live_cache_hits_total` when a waiter receives a shared in-flight result, not only on a stored-entry hit (backend/app/live/cache.py:41-58). This is minor, but the metric is documented only as cache hits.

## backend/app/attribution/EGM.md

- Side Effects say "`daily_queries.py:dispatch_day`, `expire_idle` and `settle_day` enqueue `refresh_attribution_daily` jobs and update `aggregate_job_progress`". Only `dispatch_day` enqueues (backend/app/attribution/daily_queries.py:67-83). `expire_idle` and `settle_day` only mutate the already-locked progress row in memory and write nothing themselves (backend/app/attribution/daily_queries.py:48-64, 94-105).
- The Overview's caller list omits `app/exports/service.py` and `app/exports/schemas.py`. Both import from this package, and the export service runs `check_request` before queueing (see the `check_request` docstring, backend/app/attribution/service.py:56-60).
- `daily_queries.py:locked_progress` raises `RuntimeError` when the `attribution_daily` progress row is missing (backend/app/attribution/daily_queries.py:24-32), and `dispatch_day` raises when the dedupe key already exists (:79-80). The contract describes the locking but not that a missing admission row or a key collision fails the job instead of being skipped.

## backend/app/budgets/EGM.md

- Invariants say the guard "admits a call with no price or a zero estimate without reserving anything". It also admits, without reserving, any call where `body`, `trace_id` or `span_id` is not passed (backend/app/budgets/guard.py:264-265). The only refusal on that path is a firing budget, so the budget is a soft cap for callers that omit them. The contract does not say this.
- Side Effects say `jobs.py:prune_budget_counters` "deletes old `budget_reservations`, plus ... rows older than 62 days". The 62-day `COUNTER_RETENTION` cutoff applies only to counters, deleted spans and bucket rows. Reservations are deleted on a different rule: `expires_at < now - MAX_SPAN_AGE + RESERVATION_LEASE` and no stored span with the same natural key (backend/app/budgets/jobs.py:215-246). This is a separate retention horizon of about 90 days.
- The interface lists only `rule_sync.py:scope_filters` (and `reclassify_history` under side effects). `rule_sync.py:create_budget_rule`, `sync_budget_rule` and `budget_rule_values` (backend/app/budgets/rule_sync.py:54-104) are omitted. They create and keep in sync the hidden `kind=budget` alert rule (window, filters, channels, threshold). `queries.py:trace_dimensions`, `is_gateway_key_of` and `count_budgets` (backend/app/budgets/queries.py:106-145) are omitted as well.

## backend/app/prompts/EGM.md

- Side Effects say only `create_version` updates `prompts.updated_at`. `set_label` (through `_point_label`) and `delete_label` also bump `prompts.updated_at` (backend/app/prompts/service.py, `_point_label` and `delete_label`). `queries.py:list_prompts` orders and paginates on `updated_at DESC, id DESC` (backend/app/prompts/queries.py:40-75), so moving or deleting a label reorders the list. The contract does not cover this.
- `usage.py:versions_usage` raises `ValueError` when given more than `MAX_PAGE_SIZE` version ids (backend/app/prompts/usage.py, start of `versions_usage`). It also returns a zero-usage `VersionUsage` (`cost_usd=None`, `p95_ms=None`) for ids with no spans. Neither limit nor default is in the contract.
- The `errors.py` list omits the template errors that `create_prompt` and `create_version` raise: `InvalidTemplateError`, `MissingVariablesError` and `TemplateTooLargeError` are defined in `templates.py` (backend/app/prompts/templates.py:28-44), not `errors.py`. The doc names them under `templates.py:compile` but not as service errors, although `create_prompt` / `create_version` raise `InvalidTemplateError` through `extract_variables`.

## backend/app/telemetry_losses/EGM.md

- The Overview says ingest records its losses "in the batch transaction" and only "the gateway counts losses in an in-process counter". The ingest quota caps also write `ingest` losses (`quota_rate`, `quota_spans`, `quota_bytes`) through the gateway's process-wide `LossCounter`, via `app.gateway.span_losses.loss_counter()`. They are not written in a batch transaction (backend/app/ingest/quota.py:14-15, 303, 365). These losses reach `telemetry_losses_hourly` only through the minute flush, and they are recorded even though the request is refused. This contradicts the invariant that the `service.py` path means "a failed ingest batch records no loss", because that invariant does not cover quota refusals.
- `counter.py:LossCounter` is described as the counter "that `app.gateway.span_losses` instantiates". The single instance is shared by the gateway recorder and `app/ingest/quota.py`, and the counter's own docstring names ingest as a hot-path caller (backend/app/telemetry_losses/counter.py:1-4). The contract's caller list omits ingest quota.
- `app/process_shutdown.py` imports `flush` to join the final flush at shutdown (backend/app/process_shutdown.py:19). The contract mentions `process.shutdown_incomplete` but not this caller.

## backend/app/backups/EGM.md

- The Overview presents pruning as part of the nightly backups ("streams `pg_dump` output ... prunes old dumps under a daily and weekly retention policy"). The `backup_database` job never prunes. `run_backup_database` only dumps (backend/app/backups/jobs.py:19-41). `retention.py:prune_backups` is called only by the `spanlight backup prune` CLI command (backend/app/cli.py:44, 606-617). Without an operator running prune, old dumps accumulate.
- The Overview says the CLI calls this package "for `backup-*`". The commands are subcommands of a `backup` Typer group, `spanlight backup now|list|prune` (backend/app/cli.py:89-90, 576, 594, 606), not `backup-*` top-level commands.
- `jobs.py:run_backup_database` returns `JobOutcome.OK` on success (backend/app/backups/jobs.py:41). The job is scheduled daily with `max_attempts=3` and a 3-hour `run_after_offset` (backend/app/jobs/scheduler.py:43-44). The contract says only "scheduled daily".

## backend/app/pricing/EGM.md

- Invariants say "`tiers.py:multipliers_for` uses the snapshot named by the span's version". That holds only for seed versions. For an override version (`override:<id>`) or an unknown version, it uses the snapshot in force at `at`. Before every snapshot it returns neutral factors of 1. A snapshot without its own table falls back to the oldest table any snapshot carries (backend/app/pricing/tiers.py:155-170). Override-priced spans therefore get tier and modifier factors from time, not from their version.
- Invariants say `PriceBook.cost` leaves a span unpriced when "a count exceeds `MAX_PRICED_TOKENS`". Only `input_tokens` and `output_tokens` are checked. Cached and cache-write counts are not compared to the cap (backend/app/pricing/cost.py:162-163).

## backend/app/notifications/EGM.md

No drift detected.

## backend/app/email/EGM.md

No drift detected.

## backend/app/email/providers/EGM.md

- Invariants say `smtp.py:SmtpEmailSender._build` "rejects" CR or LF in `to`, `subject` and headers, and a `to` that is not exactly one mailbox. It rejects them by raising `EmailDeliveryError` (backend/app/email/providers/smtp.py, `_build`). That is a plain, retryable `DeliveryError`, not a `PermanentDeliveryError`, so the outbox retries a malformed message up to `MAX_ATTEMPTS` (8) times with backoff (backend/app/notifications/delivery.py, `_send` marks a failure permanent only for `PermanentDeliveryError`). The contract does not say these rejections are retried, which is the opposite of the parent package's rule that unfixable rows fail permanently.
- `smtp.py` skips STARTTLS when the port is 465 (implicit TLS), even if `smtp_starttls` is true (backend/app/email/providers/smtp.py, `_submit` / `_implicit_tls`). The contract lists STARTTLS and implicit TLS separately but does not state that port 465 overrides the setting.

## backend/app/services/EGM.md

- Invariants say "Notice functions mail only owners with a verified email and never the acting user". `member_notices.py:enqueue_member_removed_notices` also mails the removed member when their email is verified, and on a self-leave the removed member is the acting user (backend/app/services/member_notices.py:15-44, recipients extended at :34-37). `invites.py:enqueue_invite_email` mails the invitee, who is not an owner (backend/app/services/invites.py:53-68). `enqueue_member_joined_notices` excludes the joining member, not the actor (backend/app/services/invites.py:71-82).
- Invariants say session tokens "are stored only as `token_digest`". The column is `sessions.token_hash`. `token_digest` is the hashing function applied before lookup (backend/app/services/sessions.py:63-70). The behaviour matches, but the cited name is not a stored field.


## backend/app/db/EGM.md

- Coverage: `session.py:create_engine` is described only as building an engine "with pool settings". The defaults a caller relies on are not stated: `max_overflow` defaults to `pool_size` (the pool may double), `pool_timeout` is 30 s and `pre_ping` is on (`backend/app/db/session.py:16-19`, `:42`).
- Coverage: the doc omits that a statement cancelled by the cap (`QueryCanceled`) is mapped by the API to `503 SERVICE_UNAVAILABLE`, which the `timeouts.py` module contract states (`backend/app/db/timeouts.py:9-10`).

## backend/app/db/models/EGM.md

- Hallucination: the doc says `telemetry.py:Span` stores `gateway_key_id` and `prompt_version_id` as plain UUIDs with no foreign key. `Span` has no `gateway_key_id` column. The gateway key is stored in `source_key_id` (`backend/app/db/models/telemetry.py:232-234`). Only `prompt_version_id` matches (`telemetry.py:226`).
- Coverage: `Span` columns that callers read or must never write are missing: `usage_rollup` (derived by the ingest recompute, never written by a client, skipped by sums; `telemetry.py:227-231`), `payload_bytes` (`telemetry.py:218`) and `request_hash` / `finish_reason` (`telemetry.py:222-223`). `Trace.retry_cost_usd` is nullable like the other cost columns but is not listed (`telemetry.py:89`).
- Coverage: the package exports enums that the Interface omits: `BillingProvider`, `BillingSourceKind`, `FetchTrigger`, `LineKind`, `RunStatus`, `SourceStatus`, `TokenType`, `EvaluatorKind`, `EvaluatorRunSource`, `EvaluatorRunStatus`, `ExperimentRunStatus`, `ExportFormat`, `ExportKind`, `ExportStatus`, `FaultScenario`, `ProviderKind`, `ReplayFidelity` and `ReplayStatus` (`backend/app/db/models/__init__.py:26-118`).
- Ambiguity: `scores.py: ScoreConfig and Score [P]` and `review.py: ReviewPolicy and ReviewSelection [P]` read as if only the second table has `project_id`. `score_configs` and `review_policies` also have a `project_id` column with ON DELETE CASCADE.

## backend/app/api/EGM.md

- Incorrect behaviour: the doc says `_member_access` refuses a `read` token on "non-GET methods". The code allows GET, HEAD and OPTIONS (`_SAFE_METHODS`) and refuses every other method (`backend/app/api/deps.py:70`, `:577-585`).
- Incorrect behaviour: the doc says `gw_errors.py:_database_error` "logs database failures and answers 503". It answers 503 with `Retry-After` only for busy errors (pool timeout, lost connection, statement timeout, unreachable). Any other database error becomes a logged 500 through `_unexpected` (`backend/app/api/gw_errors.py:97-112`).
- Incorrect behaviour: the doc says `gw.py:_authenticate` checks "the org, RPM and TPM limits". The TPM window is checked only for model calls (`check_tokens=model_call`). `GET /gw/v1/models` skips it and records no refusal span (`backend/app/api/gw.py:142-155`, `:180-182`).
- Coverage: the Overview says `main.py` mounts `health`, `ingest` and `scores_ingest` before `v1.router`. It also mounts `prompt_read_router` (`GET /v1/prompts/{name}`) and every router from `ee.routers()`. A separate standalone gateway app mounts only `gw.router` and `health.router` and always installs the gateway error handlers (`backend/app/main.py:178-190`, `:273-283`).
- Coverage: `ingest_max_in_flight_per_project` is enforced by a per-process dict (`_project_in_flight`), not across replicas. Its refusal is a 429 with `Retry-After: 1` and the metric label `ingest_project_concurrency` (`backend/app/api/ingest.py:74`, `:92-96`). The doc does not say it is per process.
- Coverage: if the project is deleted after the key check, `ingest.py:_store` and `scores_ingest.py:ingest_scores` answer 401 (`backend/app/api/ingest.py:195-196`, `backend/app/api/scores_ingest.py:126-128`). This is not documented.
- Coverage: `scores_ingest.py` spends the same `ingest:key:<id>` bucket as span ingestion (`backend/app/api/scores_ingest.py:59-65`). The Side Effects list names only `ingest.py:authenticate_ingest`.
- Coverage: the metric list omits `spanlight_ingest_quota_replays_admitted_total` (`INGEST_QUOTA_REPLAYS_ADMITTED`, `backend/app/api/ingest.py:227-228`).
- Coverage: the readiness contract of `GET /health/ready` is undocumented. It answers 503 when the database or migrations are not ok, when `WORKER_REQUIRED` is set and the heartbeat is older than 120 s, or when more than 1000 outbox rows are overdue. It also reports `gateway_mode`, `gateway_spans_lost` and `aggregate_coverage` (`backend/app/api/health.py:25-32`, `:46-83`).
- Coverage: `TraceFilters` with `q` sets a transaction statement limit (`SEARCH_TIMEOUT_MS`) and turns a timeout into `422 SEARCH_TOO_BROAD` (`backend/app/api/trace_filters.py:106-118`). This is not mentioned.
- Coverage: the gateway router has a catch-all for trailing-slash paths that answers a provider-shaped 404 (`backend/app/api/gw.py:284-290`). This is not mentioned.
- Coverage: the `*_problems.py` and `dataset_deps.py` modules also export non-mapper helpers that routers use: `dataset_deps.page_of`, `log_dataset_event`, `DEFAULT_PAGE` (`backend/app/api/dataset_deps.py:18-31`); `prompt_problems.cursor_version`, `log_prompt` (`backend/app/api/prompt_problems.py:78-92`); and `evaluator_problems.actor_of` (`backend/app/api/evaluator_problems.py:39`).

## backend/app/api/v1/EGM.md

- Incorrect behaviour: account-owner proof is stated as unconditional for `keys.py:create_key` and `gateway_credentials.py:create_credential`. It is conditional. `keys.create_key` requires a session and `confirm_account_owner` only when the requested scopes include a read scope (`backend/app/api/v1/keys.py:81-92`). `gateway_credentials.create_credential` requires them only when a custom `base_url` is given (`backend/app/api/v1/gateway_credentials.py:170-189`).
- Coverage: the list of routes that require account-owner proof is incomplete. `confirm_account_owner` is also called by `gateway_keys.py:create_key` and `update_key` (when the change widens the key), `orgs.py:remove_member`, `orgs.py:create_invite`, `oauth.py:link`, `totp.py:totp_setup`, `tokens.py:create_token`, `audit_streams.py:rotate_audit_stream_secret` and `delete_audit_stream` (`gateway_keys.py:171`, `:230`; `orgs.py:454`, `:519`; `oauth.py:159`; `totp.py:129`; `tokens.py:53`; `audit_streams.py:202`, `:255`).
- Incorrect behaviour: the doc says `live.py:live_stream` "allows 3 streams per session". A fourth stream is not refused. The oldest stream is evicted (`event: evicted`) and the new one opens. `503 LIVE_BUSY` (with `Retry-After: 30`) is the per-process `LIVE_MAX_STREAMS` limit, not the per-session one (`backend/app/api/v1/live.py:9-11`, `:112-122`).
- Incorrect behaviour: the doc says upload routes "release the database connection before reading the body", citing `dataset_items.py:_parse_upload`. Only `billing_source_runs.upload_csv` commits before it reads the body (`backend/app/api/v1/billing_source_runs.py:263-273`). `dataset_items.upload_items` takes the file as a FastAPI `UploadFile` parameter and has no explicit release (`backend/app/api/v1/dataset_items.py:145-157`).
- Coverage: the "secrets are returned once" list omits uncacheable secret responses in `alerts.py:update_channel` (secret rotation), `audit_streams.py:put_audit_stream` and `rotate_audit_stream_secret`, `gateway_keys.py:create_key`, `billing_sources.py` and `exports.py` (`alerts.py:223`, `audit_streams.py:177`, `:233`, `gateway_keys.py:200`, `billing_sources.py:84`, `:110`, `exports.py:95`, `:117`).
- Coverage: key-cache invalidation is also done by account deletion and member removal (`key_cache.evict`) and by org deletion (both caches cleared) (`backend/app/api/v1/account.py:390`, `orgs.py:275-276`, `orgs.py:491`). The Side Effects list names only key, credential, route, fault-profile and project changes.
- Coverage: the doc does not say what principal `POST /orgs` and `POST /invites/preview|accept` take. All three are session-only (`CurrentSession`). Some routes are not listed at all: `GET /insights/summary`, `GET /review-policy/selections`, `GET /evaluator-agreement`, `GET /detector-sensitivity/{detector}/preview`, `POST /score-configs/failure-category`, the gateway route `versions|revert|default` routes and the `providers/errors/*` reads are not listed.
- Coverage: `billing_source_problems.py:throttle_key_check` and `source_problem` are referenced only in prose. `BILLING_UPLOAD_LIMITER` (six uploads per source per hour) and `UNSUBSCRIBE_LIMITER` are missing from the limiter list (`backend/app/api/v1/billing_source_runs.py:256`, `:266-272`; `account.py:156-162`).

## backend/app/api/schemas/EGM.md

- Incorrect behaviour: the doc says "only create responses carry a secret once" and names `GatewayKeyCreatedOut.secret`, `ApiKeyCreatedOut.secret` and `PersonalAccessTokenCreatedOut.token`. Other responses also carry secrets: the alert channel response (`secret` on create and rotation, `backend/app/api/schemas/alerts.py:45-47`), the audit stream response (`secret` on create and rotation, `orgs.py:194-195`), TOTP setup (`secret` and `otpauth_url`) and enable (`recovery_codes`) (`auth.py:146-153`), and the invite-create `url`, which carries the invite token (`orgs.py:97`).
- Incorrect behaviour: the doc says `insight_feedback.py:_decimal` produces fixed-place decimal strings. It returns `str(value)` with no quantize, so its places and format follow the source `Decimal` (`backend/app/api/schemas/insight_feedback.py:113-114`).
- Coverage: the list of modules imported directly by routers is incomplete. Routers also import directly from `alert_deliveries`, `auth`, `billing_sources`, `common`, `experiments`, `gateway`, `ingest_quota`, `insight_feedback`, `insights`, `orgs`, `playground`, `prompt_versions`, `providers`, `replays`, `review`, `scores` and `traces`.
- Coverage: budget and alert money fields use `app.alerts.payload.DecimalStr`, not `common.py:Money` (`backend/app/api/schemas/budgets.py:14`, `:48`). `common.py` also re-exports `Password` and `TotpCode` (`common.py:14-16`). Neither is mentioned.

## backend/app/jobs/EGM.md

- Coverage: `worker_lifecycle.py` calls `ee.load(settings, ...)` and builds the worker with `handlers={**TASKS, **ee.tasks()}`. Enterprise tasks are merged into the registry (`backend/app/jobs/worker_lifecycle.py:110`, `:130`). The doc does not say so.
- Coverage: `worker_progress.py:ShortLoopProgress` (`POLL_STALL_SECONDS` 65.5) is not described, though its metric `spanlight_worker_short_loop_stalls_total` is listed (`backend/app/jobs/worker_progress.py:29-54`).
- Coverage: `queue.py:enqueue` defaults `max_attempts` to 5. `claim_next` also re-claims running jobs whose lease expired with attempts left, honours `exclude_kinds`, and orders by `run_after, id` (`backend/app/jobs/queue.py:56`, `:116-120`). None of this is stated.
- Coverage: `outcome.py:JobOutcome.is_skipped` excludes `already_selected`. The worker counts it as `done` in `spanlight_jobs_finished_total`, not as `skipped` (`backend/app/jobs/outcome.py` `is_skipped`, `backend/app/jobs/worker.py:198`).
- Coverage: `project_pass.py` also has a 30 s per-project timeout (`PROJECT_TIMEOUT_SECONDS`), not documented (`backend/app/jobs/project_pass.py:37`, `:105`). `aggregate_progress.py` exports `heartbeat`, `set_limits`, `remaining` and the bounds `START_SECONDS` 30 / `OUTER_SECONDS` 40, which retention reuses (`backend/app/jobs/aggregate_progress.py:13-21`).
- Coverage: `metrics_server.serve_metrics` does nothing when no port is configured (`backend/app/jobs/metrics_server.py:86-87`). The worker also logs `job_lease_lost`, `job_lease_lost_on_complete|fail|retry` and `long_job_crashed`, which are missing from the log list (`backend/app/jobs/worker.py:116`, `:170-196`).

## backend/app/jobs/tasks/EGM.md

- Incorrect behaviour: the doc says `seed_demo_evals.py:run_seed_demo_evals` has a 120 s limit. `seed_demo_evals` is not in `worker.py:TASK_TIMEOUT_OVERRIDES`, so the worker cancels it at `TASK_TIMEOUT_SECONDS` (50 s) before the handler's own 120 s timeout can apply (`backend/app/jobs/tasks/seed_demo_evals.py:25`, `:35`; `backend/app/jobs/worker.py:48-62`, `:167-168`).
- Coverage: retention also deletes telemetry losses alongside hourly rollups (`app.rollups.retention.delete_hourly_rollups`), per the module contract (`backend/app/jobs/tasks/retention.py:3-4`, `:239-244`). Detector runs use a fixed 7-day retention (`DETECTOR_RUN_RETENTION`), not the project policy (`backend/app/jobs/tasks/retention_sql.py:49`; `retention.py:97`). Both are missing from Side Effects and Invariants.
- Coverage: the Side Effects list for `cleanup.py:cleanup` omits `sdk_exporter_counters` and `ingest_quota_days`, which `cleanup()` itself prunes. It also omits `insight_explanations` (stale explain reservations completed at worst-case cost after 10 minutes) and budget counters changed by `run_cleanup_sessions` (`backend/app/jobs/tasks/cleanup.py:53`, `:63-70`, `:142-143`).

## backend/app/gateway/EGM.md

- Incomplete enum: the `errors.py` code list omits `NO_COMPATIBLE_TARGET` (400) and `UPSTREAM_BLOCKED` (502), which are in `_TABLE` (`backend/app/gateway/errors.py:87`, `:91`). It also omits the HTTP-layer codes `NOT_FOUND` (404), `METHOD_NOT_ALLOWED` (405) and `SERVICE_UNAVAILABLE` (503, also returned by `execute` when the database is busy) (`errors.py:215-245`; `backend/app/gateway/execute.py:74-79`).
- Coverage: `http.py:build_http_client` also refuses private and local network addresses through the vetted egress transports (`allow_private=allow_insecure`, giving `UPSTREAM_BLOCKED`) (`backend/app/gateway/http.py:14-20`, `:51-65`). This egress rule is not in the Invariants.
- Coverage: when the gateway injects `stream_options.include_usage`, `usage_chunk.py:UsageChunkFilter` strips the extra usage-only chunk from the client's stream (`backend/app/gateway/usage_chunk.py:1-12`). The doc mentions only that the option is added, not that the response is altered.
- Coverage: these modules are not mentioned: `usage_chunk.py`, `span_usage.py` (cache hits record zero usage; an unknown output count stays None), `provider_attributes.py` (span `spanlight.gateway.status` attributes), `streaming.py`, `record_tasks.py`, `write_evidence.py`, `key_errors.py` and `fault_errors.py`.
- Incorrect behaviour: the doc implies the key TPM limit spends `rate_limit_buckets` through `GatewayLimiter.take`. Only the org ceiling and key RPM use buckets. TPM is checked with `check_tpm` against `gateway_key_minutes` (`backend/app/gateway/key_limits.py:71-74`, `:113-123`).
- Coverage: `recorder.py:record_soon` drops a `low_priority` span when the backlog is half full (`backend/app/gateway/recorder.py:115-116`). The metric list omits `spanlight_gateway_admission_duration_seconds` (`backend/app/core/observability.py:162-163`, used by `admission.py`).

## backend/app/gateway/adapters/EGM.md

- Incorrect caller list: the doc says the attempt loop and the credential check look adapters up with `adapter_for`. `models_endpoint.py` also calls it for `GET /gw/v1/models` (`backend/app/gateway/models_endpoint.py:25`, `:137`).
- Coverage: `__init__.py` also exports `CredentialEndpoint`, the protocol for the credential fields an adapter reads (`backend/app/gateway/adapters/__init__.py:6`, `base.py` `CredentialEndpoint`). `ProviderAdapter` also has a `provider` property, which the protocol list omits.
- Coverage: `send_upstream` can raise `app.core.egress.BlockedAddress` for a blocked destination as well as httpx transport errors (`backend/app/gateway/adapters/base.py:198-201`). This is not in the contract.

## backend/accuracy/EGM.md

- The doc says the `doctor` subpackage has "labelled scenarios for every Doctor detector". The product catalogue has 16 detectors, including `model_retiring` (`backend/app/insights/engine.py:59-76`, line 75). The harness lists 15 and has no `model_retiring` scenario (`backend/accuracy/doctor/scenarios.py:265-281`; no match for `model_retiring` anywhere under `backend/accuracy` or `backend/tests/accuracy`).
- Verification says `uv run pytest tests/accuracy` covers this package. That directory tests only the Doctor modules (`test_collect.py`, `test_incident_feed.py`, `test_scenarios.py`, `test_score.py`, `test_synthetic.py`). Nothing there imports `accuracy.ledger`, `accuracy.setup`, `accuracy.paths` or `accuracy.dashboard`, so the spend-cap invariants in the doc have no test.
- The ledger invariants leave out the Haiku 5.5 price tier. `HAIKU_55_THRESHOLD = 100_000` and the `claude-haiku-5-5>100k` row (`backend/accuracy/ledger.py:55-64`) pick the higher rates once a prompt passes 100,000 tokens (`ledger.py:75-79`). `worst_case` always prices at `max(padded, HAIKU_55_THRESHOLD + 1)`, so a Haiku 5.5 reservation always uses the >100k row. It also pads the estimate by 20 % plus 50 tokens (`ledger.py:139-141`). The doc says only "padded prompt" and does not name the threshold.
- The doc says "models without a published rate are refused". `base_model` accepts any model that starts with a known name followed by `-` (`ledger.py:70`), not only dated snapshots, so `claude-haiku-4-5-anything` is priced as `claude-haiku-4-5`.
- The doc says the state file is "saved atomically with mode 0600". `State.save` passes mode `0o600` to `os.open(..., O_CREAT)` on a fixed `.tmp` path (`backend/accuracy/setup.py:36-40`). That mode applies only when the temp file is created. A leftover `state.tmp` with wider permissions keeps them and is then renamed over the state file, which holds a password and a PAT.

## backend/accuracy/doctor/EGM.md

- The doc says the harness "measures the precision and recall of every Doctor detector". `model_retiring` (`backend/app/insights/engine.py:75`) has no scenario and is not in `DETECTORS` (`backend/accuracy/doctor/scenarios.py:265-281`).
- The doc says "each labelled scenario owns one project named `dr-<detector>-<case>`". Every name gets `DR_SUFFIX` appended (`scenarios.py:55-59`, `scenario_types.py:100`). Each `provider_incident` scenario also owns a second project, `dr-provider_incident-<case>-b<suffix>` (`scenarios.py:68-70`, `scenarios.py:231-250`), in its own organization.
- The doc says `--only` accepts "scenario ids, detectors or project names". `wanted_by` matches only `scenario.project`, the first project (`runner.py:229-230`). A second project name such as `dr-provider_incident-pos-b` is rejected as unknown (`runner.py:223-225`).
- Collect reads only findings with status `open` (`collect.py:91`, `common.py:122-135` default `("open",)`). A finding that was acknowledged, resolved or muted before collect counts as "did not fire". The doc does not say this.
- In Side Effects the doc says only "inserts into and polls the `jobs` table". The harness connects as the `postgres` superuser to the database `spanlight` (`trigger.py:32-33`) and inserts each job with `max_attempts = 1` (`trigger.py:35-37`). Passing `--rollup-only` and `--detectors-only` together enqueues nothing and exits 0 (`trigger.py:125-135`).
- `PatApi.get` retries a 429 up to 8 times and sleeps for the `Retry-After` value, capped at 30 s (`common.py:111-120`). Ingest sleeps 50 ms after each batch to stay under the per-key rate limit (`common.py:169`). The Interface and Invariants sections leave out both behaviours.
- Verification (`pytest tests/accuracy`) covers `collect`, `score`, `scenarios`, `traffic_synthetic` and `incident_feed`. No test covers `clients.py` (the ledger-guarded call paths the Invariants rely on), `runner_setup.py` (the idempotent setup) or `trigger.py`.

## backend/alembic/EGM.md

- The doc says `app/db/migrations.py:head_revision` and `downgrade_to` "serve tests". `head_revision` is also on the product path: the health readiness probe compares the database revision with it (`backend/app/api/health.py:17`, `health.py:112`).
- The Overview says "the application, the CLI and the tests apply migrations through `app/db/migrations.py`". The API process never applies migrations; it only reads the head revision. They are applied by the CLI (`backend/app/cli.py:426-430`), by the Compose `migrate` service through that CLI (`deploy/compose.yaml:162-169`) and by tests.
- The doc says "the engine ... is disposed after the run". `engine.dispose()` is not in a `finally` block (`backend/alembic/env.py:29-34`), so a failed revision skips it.
- Interface leaves out two details of `alembic_config`: it escapes `%` as `%%` before setting `sqlalchemy.url` (`backend/app/db/migrations.py:17`), and `head_revision` is `lru_cache`d for the life of the process (`migrations.py:21-23`).

## backend/alembic/versions/EGM.md

- The doc says there are "107 revisions". There are 106 revision files (`0001_initial_schema.py` to `1045_add_key_scopes_change.py`). The chain is linear with one head, `1045`, and each `down_revision` is the previous file in numeric order, so the head claim is correct.
- The doc says revisions that alter a live table set `SET LOCAL lock_timeout`. 39 of the 106 files never set `lock_timeout`, including some that alter populated tables: `0007_add_api_key_scopes.py:45-52` (`ALTER TABLE api_keys`), `0009_allow_project_delete_audit.py:67-71` (`audit_events`), `0013_add_job_outcome.py:29-34` and `1003_widen_job_outcome.py:32-37` (`jobs`). The head revision itself uses a session-level `SET lock_timeout` followed by `RESET` inside the autocommit block, not `SET LOCAL` (`1045_add_key_scopes_change.py:137-141`). Every value that is set is 5 s.
- The doc says new CHECK constraints on populated tables are added `NOT VALID` and validated separately in an autocommit block. Only 20 revisions use `NOT VALID`. Several add a validating CHECK inside the migration transaction: `0007_add_api_key_scopes.py:50-51`, `0009_allow_project_delete_audit.py:68-71`, `0300_add_outbox_fence.py:61-63`, `0801_add_promoted_metadata_keys.py:52-56` and `1013_harden_audit_events.py:191-192`. `1003_widen_job_outcome.py:33-37` adds the CHECK `NOT VALID` but then validates it in the same transaction.
- The doc says a downgrade that would lose data or break the append-only audit chain refuses with `RAISE EXCEPTION ... retain migration NNNN` instead of deleting rows. Only 5 revisions do that: `1027`, `1028`, `1040`, `1041` and `1045`. 20 revisions' downgrades run `DELETE FROM` instead, including `audit_events` rows: `0005_add_oauth_identities.py:108`, `0009_allow_project_delete_audit.py:61`, `0200_add_provider_credentials.py:120` and `1013_harden_audit_events.py:181`. `1014_chain_and_stream_audit_events.py:264-266` disables the `audit_events_append_only` trigger and deletes audit rows.
- The doc gives `0007_add_api_key_scopes.py:upgrade` as an example of a revision that backfills rows with `UPDATE`. Its `upgrade` only adds columns and a CHECK (`0007_add_api_key_scopes.py:43-52`). The `UPDATE api_keys SET revoked_at = now()` is in `downgrade` (`0007_add_api_key_scopes.py:56-65`), where it revokes keys.
- The doc says `SECURITY DEFINER` functions "restore the caller's value on success and on error" and cites `0507_add_explain_spend_function.py:upgrade`. 0507 has no `EXCEPTION` block; it restores `app.bypass_rls` only on the success path (`0507_add_explain_spend_function.py:58-68`). Only 1044 restores on error (`1044_add_live_markers.py:62-64`).
- The doc says "a new function stays uncallable until it is listed" in `APP_FUNCTIONS`. That holds only for functions created with `REVOKE ALL ... FROM PUBLIC`. `merge_trace_metadata(jsonb, jsonb)` (`0800_add_trace_metadata.py:28-35`, `0800_add_trace_metadata.py:53`, `0800_add_trace_metadata.py:75`) and `spanlight_search_text(jsonb, jsonb)` (`1007_add_spans_content_search_index.py:53`) keep the default PUBLIC `EXECUTE` grant. The app calls both (`backend/app/ingest/store.py`, `backend/app/ingest/metadata.py`, `backend/app/explorer/content_search.py`), but they are missing from the doc's list of "database functions ... that the app role calls" and from `backend/app/db/roles.py:20-27`.
- The doc says every project-scoped table gets `ENABLE` and `FORCE ROW LEVEL SECURITY` with exactly the two policies `_project_isolation` and `_worker_bypass`. At head (checked read-only on the dev database at revision 1045), three tables with a `project_id` column do not force RLS: `api_keys`, `audit_events` and `billing_source_projects`. `gateway_keys` has a third, SELECT-only policy, `gateway_keys_prefix_lookup` (`0202_add_gateway_keys.py:84`). The doc does not list these exceptions or say why they exist.
- 1007 is the only revision that alters a `pg_catalog` function. When run as a superuser it marks `pg_catalog.texticlike(text, text)` and `spanlight_search_text` `LEAKPROOF`; otherwise it raises a WARNING and skips that step (`1007_add_spans_content_search_index.py:61-70`). `ensure_app_role` applies the mark again after restores (`backend/app/db/roles.py:85-97`). The doc's rules do not mention this.
- Side Effects describes the 1013 append-only trigger but not the audit hash chain that 1014 adds: the `audit_event_digest` function, the `audit_events_chain` BEFORE INSERT trigger and the `audit_streams` table (`1014_chain_and_stream_audit_events.py:146`, `1014_chain_and_stream_audit_events.py:173`, `1014_chain_and_stream_audit_events.py:233-238`). The 1013 exemption is also wider than "foreign-key cascades": any call at `pg_trigger_depth() > 1` passes (`1013_harden_audit_events.py:157-160`).

## backend/load/EGM.md

- The doc says seeders "connect as the database owner and bypass the application role's row-level security by design". The Python seeders set the RLS settings themselves through `app.db.rls`. `bind_project` is called at `seed.py:284`, `seed_alerts.py:121` and `seed_tour.py:440`, and `bypass_rls` at `seed_gateway.py:216`. With `FORCE ROW LEVEL SECURITY`, a table owner that is not a superuser is still filtered, so those calls are what the seeds depend on.
- The `seed_gateway.py` entry leaves out the `--provider-base-url` flag (default `http://fake-provider:9000/v1`, `seed_gateway.py:71`, `seed_gateway.py:271-276`). It also leaves out that `--budget-spans` sets the project's `retention_days` to 31 (`seed_gateway.py:76`, `seed_gateway.py:217-221`), inserts spans as well as traces (`seed_gateway.py:81-104`) and runs `VACUUM ANALYZE spans` (`seed_gateway.py:262-264`). Side Effects names only `seed.py:vacuum_analyze`.
- The `seed_tour.py` entry leaves out three things: the `--login-users` flag (default `DEFAULT_LOGIN_USERS = 6`, `seed_tour.py:107`, `seed_tour.py:413-419`); the `openai` credential it seals with `LOAD_CREDENTIALS_KEYS` (`seed_tour.py:14-19`, `seed_tour.py:337`, `seed_tour.py:428`); and the precondition of at least 600 seeded traces with an LLM span (`seed_tour.py:444-449`).
- The doc lists the gateway overhead limits (`OVERHEAD_LIMIT_MS` 20, `BUDGET_OVERHEAD_LIMIT_MS` 5) under "k6 thresholds fail the run". k6 does not enforce them: `handleSummary` only stores `overhead.ok`, and the workflow fails the job with `jq` (`.github/workflows/load-smoke.yml:242-243`). When `GATEWAY_VUS` is set, the p95 limits are replaced by thresholds that always pass (`gateway.js:131-132`). `tour.js:P95_LIMIT_MS` applies only to read endpoints; the write scenarios `otlp_json`, `otlp_protobuf` and `scores` have no latency threshold (`tour.js:126-131`).
- The doc says CI runs the kit. The workflow runs `overview`, `traces`, three `mixed` variants, `ingest` and `gateway` (`.github/workflows/load-smoke.yml:212`). It never runs `seed_tour.py` or `tour.js`, and `mixed-near-knee` is not gated (`load-smoke.yml:219`, `load-smoke.yml:233`).
- The doc says `compose.load.yaml` only publishes Postgres, passes the keyring and adds `fake-provider`. It also sets `DEMO_ENABLED: "false"` and `GATEWAY_ALLOW_INSECURE_BASE_URLS: "true"` on `api` and `worker` (`backend/load/compose.load.yaml:19-31`).
- The doc says "the same seed and batch index always produce the same spans". `build_batch` also takes `now`, `count` and `batches` (`seed_data.py:215-233`), and timestamps are placed relative to `now`, so runs at different times produce different spans.
- The doc says `seed.py:Progress.add` "stops the seed when the projected time passes `--max-minutes`". It checks only every `PROGRESS_EVERY = 1_000_000` spans (`seed.py:62`, `seed.py:146-148`), so a seed under one million spans (the default is 100,000) can never stop early. With `--processes > 1`, a failing process does not stop the others (`seed.py:241`).
- `settle.sh` exits 0 at once, without waiting, when `LOAD_ENV_FILE` is unset (`settle.sh:27-30`). It queries as `-U postgres -d spanlight` (`settle.sh:55-56`). The doc does not say either.

## backend/scripts/EGM.md

- The doc says `build_app` and `render_openapi` are imported by `tests/api/test_openapi_snapshot.py`. `build_app` is also imported by `backend/tests/keys/test_key_route_inventory.py:15`.
- The doc says `update_openapi.py:build_app` "pins every environment-dependent setting". It pins six settings and `_env_file=None` (`backend/scripts/update_openapi.py:31-39`). Every other `Settings` field is still read from the process environment, so the claim covers `.env` but not exported variables.
- The doc lists only `ci.yml` as a CI caller. `otlp_expected.py` also runs weekly in CI: `.github/workflows/otlp-matrix-weekly.yml:63` runs `record.py --check`, which calls `scripts/otlp_expected.py` (`sdks/fixtures/otlp/record/_document.py:98-105`).
- The doc says `otlp_expected.py:expected_for` "decodes, maps and normalizes exactly as `POST /v1/otlp/traces` does". It always normalizes with `capture_payloads=True` (`backend/scripts/otlp_expected.py:54-56`) and prices from the seed book only. Per-project payload capture and price overrides, which the endpoint applies, are skipped.
- The doc says `record_gateway_fixtures.py:main` refuses with exit 2 in CI. `check_cap` runs first (`record_gateway_fixtures.py:487-488`), so an out-of-range `--max-usd` exits 1 with a message, even in CI.
- The doc says fixtures are "moved into place only when every case succeeded; otherwise existing fixtures are untouched". When all cases succeed, files are moved one at a time with `shutil.move` (`record_gateway_fixtures.py:471-475`). A failure partway through that loop leaves a mix of new and old fixtures.

## frontend/src/features/settings/EGM.md

- The Interface lists the Audit log URL filters as `action`, `actor`, `since`, `until`, `outcome` and project. The schema also has a `via` filter (`user`, `token`, `cli`, `system`), and the doc does not mention it (`frontend/src/features/settings/audit-search.ts:33`).
- The `gcTime: 0` invariant names only `useRequestEmailChange`, `useDeleteAccount` and `useAuditStreamMutations`. The member mutations also carry passwords or one-time URLs and also set `gcTime: 0`, so the invariant's list is incomplete: `useUpdateMemberRole` and `useRemoveMember` (`frontend/src/features/settings/member-queries.ts:32`, `:46`) and `useCreateInvite` (`member-queries.ts:61`).
- The project-deletion invariant says the delete needs only "its typed slug". The danger zone also collects account-owner proof through `useMemberReauth` and sends it with the slug (`frontend/src/features/settings/project-danger-zone.tsx:17`, `:33-34`; `use-delete-project.ts:32`, `:37`). The re-authentication invariant and the "Used by" entry for `useMemberReauth` leave out this in-folder use.
- The doc says "the remembered last project is cleared" after a project delete. `clearLastProject({ projectId })` forgets the remembered project only when it is the deleted one (`frontend/src/lib/last-project.ts:43-56`; called at `use-delete-project.ts:38`). It also runs before the cached project list is filtered, which is the reverse of the order the doc gives (`use-delete-project.ts:38-42`).
- The doc says `member-reauth.tsx:MemberReauthFields` is used by `organization/`. No file in `organization/` imports it. Only `features/gateway/keys/create-key-form.tsx`, `features/gateway/keys/edit-key-sheet.tsx` and `features/gateway/credentials/add-credential-form.tsx` import it; `organization/danger-zone.tsx` reaches it only indirectly through `ConfirmSlugDialog`.
- Coverage: the settings nav, which this folder owns, also lists "Notification channels" and "Billing sources" pages (`settings-nav-items.ts:58-67`). Those pages come from `features/alerts` and `features/billing-sources` and are gated by `org:read`. The Overview and the nav invariant do not mention them.

## frontend/src/features/settings/organization/EGM.md

- Side Effects leaves out the organization's project list read. `OrgDangerZone` calls `features/shell:useOrgProjectsQuery(org.id)`, which runs `GET /api/v1/orgs/{orgId}/projects`, to fill the delete explanation and `ProjectsTile` (`frontend/src/features/settings/organization/danger-zone.tsx:21-23`, `:41`).
- The doc says a delete removes "every `queryKeys.project(id).all` of its projects". The ids come only from the project list already in the cache (`getQueryData(...) ?? []`). If that list was never loaded, no project cache is removed (`frontend/src/features/settings/organization/use-delete-organization.ts:29`, `:47-49`).

## frontend/src/features/settings/security/EGM.md

- The doc says TOTP errors are classified as `invalid-code`, `not-configured`, `email-unverified`, `already-enabled` and other. `classifyTotpError` also returns `invalid-password` (422 `INVALID_PASSWORD`), `rate-limited` (429) and `not-enabled` (409 `TOTP_NOT_ENABLED`) (`frontend/src/features/settings/security/totp-flow.ts:21-36`). `RATE_LIMITED_MESSAGE` and `WRONG_RECOVERY_CODE_MESSAGE` belong to these extra paths (`totp-flow.ts:47-50`).
- The connect-provider invariant names only the password and code errors. `ConnectProviderDialog` also handles `403 REAUTH_REQUIRED` (through `../account-queries.ts:isReauthRequired`) by switching to the sign-in-again path (`frontend/src/features/settings/security/connect-provider-dialog.tsx:18`, `:49`).
- Coverage: `two-factor-card-parts.tsx` exports `RecoveryCodesTile`, `VerifyEmailCallout` and `NotAvailableCallout`, and `totp-setup-footers.tsx` exports `ScanFooter`, `ConfirmFooter` and `CodesFooter`. The doc lists these files by name only, without their components (`two-factor-card-parts.tsx:26`, `:50`, `:84`; `totp-setup-footers.tsx:8`, `:27`, `:51`).

## frontend/src/features/settings/tokens/EGM.md

- The re-authentication invariant lists only `INVALID_PASSWORD`, `TOTP_CODE_REQUIRED` and `INVALID_TOTP_CODE`. `CreateTokenForm` also handles `403 REAUTH_REQUIRED` through `../account-queries.ts:isReauthRequired`. It falls back to `applyServerFieldErrors` for the `name`, `password` and `code` fields (`frontend/src/features/settings/tokens/create-token-form.tsx:23`, `:102`, `:111`).
- Side Effects says the secret is copied "through `@/components/secret-box:SecretBox`". `SecretRevealView` also renders a separate `CopyButton` that copies the usage line built from the secret. That is a second clipboard write the doc leaves out (`frontend/src/features/settings/tokens/secret-reveal-view.tsx:1`, `:27`, `:63`).

## frontend/src/features/traces/EGM.md

- Coverage: the Components list does not mention these non-test source files: `child-payload-view.tsx`, `meta-field.tsx`, `metadata-block.tsx`, `metadata-chips.tsx`, `prompt-version-ref.tsx`, `rollup-badge.tsx`, `span-detail-header.tsx`, `span-kind.tsx`, `span-metadata.tsx`, `status.tsx`, `trace-attribute-chips.tsx`, `trace-name.tsx`, `trace-retry-line.tsx`, `trace-search-input.tsx`. The test fixture `span-test-data.ts` is also unlisted, although `features/sessions` tests import it (`frontend/src/features/sessions/session-detail-page.test.tsx:14`).
- Coverage: `last-traces-search.ts` also exports `useTraceHistoryScope`, the hook that builds the user/org/project scope key the history invariant depends on. The doc's list for that file leaves it out (`frontend/src/features/traces/last-traces-search.ts:17-21`).
- Side Effects leaves out prompt-version reads. `use-trace-filters.ts` and `prompt-version-ref.tsx` call `features/prompts:usePromptVersionByIdQuery` (`frontend/src/features/traces/use-trace-filters.ts:4`, `prompt-version-ref.tsx:4`).
- Side Effects leaves out other cross-feature reads. The list page renders `features/overview/refused-spans-notice:RefusedSpansNotice` (`traces-page.tsx:9`). The empty state reads `features/shell/use-has-traces:useHasTraces` and `features/onboarding:KeySnippet` (`traces-empty-state.tsx:7-9`). The compare page imports `features/experiments/compare-tiles` and `compare-view` (`compare-totals.tsx:3-8`, `compare-step-row.tsx:9`).
- `compare-paste-form.tsx` also calls `projectsApi.compareTraces` directly through the query client before it navigates to the compare page (`frontend/src/features/traces/compare-paste-form.tsx:39`, `:49`). The doc attributes the compare request only to `useTraceComparisonQuery`.
- The waterfall invariant cites `span-waterfall.tsx:VIRTUALIZE_ROWS`. It is a module-private constant, not an exported symbol (`frontend/src/features/traces/span-waterfall.tsx:29`). The value (300) matches.

## frontend/src/features/releases/EGM.md

- The `RELEASE_WINDOW_TOO_LARGE` invariant says the error "hides the comparison". The page hides `CompareBody`, the `ReleasesTable` and the `FailureCategoriesCard`, and it marks the picker unavailable (`frontend/src/features/releases/releases-page.tsx:36`, `:59`, `:62-71`).
- Side Effects lists only reads. The "Set up failure categories" action renders `features/scores:FailureCategoriesDialog`, which creates the `failure_category` score config, a write the doc does not mention (`frontend/src/features/releases/failure-categories-card.tsx:12`, `:155-157`).
- Side Effects leaves out the stored chart/table view choice. `failure-categories-card.tsx` uses `src/lib/use-stored-choice.ts:useChartView`, which persists the viewer's choice in browser storage (`failure-categories-card.tsx:15`, `:42`).

## frontend/src/features/landing/EGM.md

- The invariant says "Every outbound and in-page link is defined in `links.ts`". Several components hard-code router links: `/login` and `/signup` in `frontend/src/features/landing/landing-nav.tsx:59`, `:62`, `:138`, `:147`, `/signup` in `hero-section.tsx:56` and `final-cta-section.tsx:34`, and `/` in `landing-nav.tsx:32`. The code samples hard-code `http://localhost:8080` endpoints (`code-samples.ts:114`, `:127`; `open-source-section.tsx:90`).

## frontend/src/features/landing/illustrations/EGM.md

- The invariant says colours come only from the classes mapped in `illo-parts.tsx` (`DOT_CLASS`, `PILL`). `sessions-illustration.tsx` defines its own `Bubble` and `MetaPill` helpers with colour classes written inline: `bg-hero-card`, `bg-surface`, `bg-surface-muted`, `text-hero-card-foreground`, `text-muted-foreground`, `border-border` (`frontend/src/features/landing/illustrations/sessions-illustration.tsx:29`, `:47`). Other files set text colours directly, for example `doctor-illustration.tsx` with `text-rail-muted-foreground` and `gateway-illustration.tsx` with `text-subtle-foreground`. These are semantic tokens, but they are not mapped through `illo-parts.tsx`.

## frontend/src/features/playground/EGM.md

- The replay-blocking invariant leaves out replay being turned off. `runDisabledReason` first refuses with `REPLAY_OFF_REASON` when the replay budget is zero. It also refuses an invalid max-tokens value (`MAX_TOKENS_REASON`), not only a value over 4096 (`frontend/src/features/playground/replay-state.ts:24`, `:102-111`).
- Side Effects leaves out the replay column's trace read: `replay-column.tsx` calls `features/traces:useTraceQuery`, which runs `GET .../traces/{traceId}` (`frontend/src/features/playground/replay-column.tsx:10`).
- Side Effects leaves out the dataset write. The invariant mentions saving a replay result to a dataset, but the doc does not say that `replay-result.tsx` does it through `features/datasets:AddToDatasetDialog` (`frontend/src/features/playground/replay-result.tsx:16`, `:115`).
- The doc says `search.ts:PlaygroundSearch` is used by `features/traces` "to build 'Replay' and 'Open in playground' links". The only importer is `features/traces/replay-button.tsx`, which builds replay links (`replay`, `edit`) (`frontend/src/features/traces/replay-button.tsx:8`, `:158`). The prompt "Open in playground" link lives in `features/prompts/prompt-overview.tsx` and does not use this type.
- `useRunReplay` invalidates `replays.all` in `onSettled`, so it also invalidates after a failed replay. "Afterwards" in the doc does not make this clear (`frontend/src/features/playground/replay-queries.ts:19-20`).

## frontend/src/features/auth/EGM.md

- Coverage: the router also mounts a legacy `/invite/$token` route. It redirects to `/invite#token=...` with `replace`, which moves old path-style invite tokens into the fragment. The doc lists only `/invite` and does not mention this redirect (`frontend/src/app/router.tsx:256-263`).
- The doc lists refetches of `queryKeys.me` "after sign-in, sign-up, demo and invite acceptance". `accept-terms-page.tsx` also calls `refreshMe` after accepting terms (`frontend/src/features/auth/accept-terms-page.tsx:38`, `:48`). `use-resend-verification.ts` also invalidates `queryKeys.me`, alongside the verify-email page (`use-resend-verification.ts:37`).
- The navigation list (`/`, `/login`, `/login/two-factor`, `/onboarding`) is incomplete. Sign-in, two-factor and terms acceptance also navigate to the validated `next` path through `postLoginDestination` and `safeNextPath` (`login-form.tsx:59`, `use-two-factor-flow.ts:86`, `accept-terms-page.tsx:33`). Sign-up can navigate to a computed destination (`signup-form.tsx:58`).

## frontend/src/features/shell/EGM.md

- The home-redirect invariant says "otherwise the first project of the first open organization". `resolveHomeDestination` orders the remembered project's organization first. It falls back to that organization's first project when the remembered project is gone. Then it walks the organizations in order and takes the first one that has any project, so an open organization with no projects is skipped. Onboarding goes to the first open organization, or to the first organization when all are locked; `/account/security` applies only when every organization is locked (`frontend/src/features/shell/home-redirect.ts:32-61`).
- The two-factor invariant says the shell's project query is "the one place that reads `403 TWO_FACTOR_REQUIRED`". `workspace-slugs.ts` and `home-redirect.ts` also treat a locked organization's project list (`403 TWO_FACTOR_REQUIRED`) as `null` through `orgProjectsOrNull`, and `src/lib/api/errors.ts:114` provides the shared predicate (`frontend/src/features/shell/workspace-slugs.ts:9`, `home-redirect.ts:18`, `:44-46`).

## frontend/src/features/experiments/EGM.md

- The search invariant mentions only run ids and the prompt name. `newExperimentSearchSchema` also requires `dataset` and `version` to be UUIDs, and the compare schema adds a free-form `score` sort key (`frontend/src/features/experiments/search.ts:14-21`).
- Side Effects names `promptPickerQuery` and `promptVersionsPickerQuery`, but `use-version-index.ts` also uses `features/prompts:promptVersionByIdQuery` (`frontend/src/features/experiments/use-version-index.ts:3-7`). The doc does not mention `features/prompts:TemplateBlocks` (`run-target-card.tsx:4`) or `features/auth:LiveDemoButton` (`experiments-page.tsx:12`) as dependencies.
- The doc says "The barrel `index.ts` re-exports the five page components". The barrel's own comment says "nothing else is shared" (`frontend/src/features/experiments/index.ts:1-4`). Yet `features/traces` imports `compare-tiles.tsx` and `compare-view.ts` directly, outside the barrel (`frontend/src/features/traces/compare-totals.tsx:3-8`, `compare-step-row.tsx:9`). The doc records that use but not the conflict with the barrel comment.

## frontend/src/features/providers/EGM.md

- The samples invariant mentions only the 20-row cap. The samples read is also cut to the window's last 24 hours (the server clamps it and reports `clamped`), and the doc does not say so (`frontend/src/features/providers/providers-queries.ts:61`; `frontend/src/lib/api/providers.ts:36`).
- Side Effects mentions only the empty state's URL write. Navigation it leaves out: the empty state also clears `from` and `to` when it writes `range` (`frontend/src/features/providers/providers-page-states.tsx:105`) and links to `/onboarding` with the org and project (`providers-page-states.tsx:113`). Each sample row links to the trace detail page (`sample-row.tsx:58-59`).

## frontend/src/features/doctor/EGM.md

- The doc says `insight-format.ts:SEVERITY_LABELS` is used by `features/traces`. It is also imported by `features/alerts` (`frontend/src/features/alerts/channel-schema.ts`, `frontend/src/features/alerts/severity-select.tsx`).
- The polling invariant names "insight lists". `useHasAnyInsight` (`limit: 1`) and `usePrecision` do not poll, while `useProjectInsights` and `useInsightPages` do (`frontend/src/features/doctor/doctor-queries.ts:29-37`, `:40-51`, `:65-74`, `:144-151`).

## frontend/src/features/datasets/EGM.md

- The doc says "Every dataset write invalidates `queryKeys.project(projectId).datasets.all`". The invalidation runs in `onSuccess` only, so a failed write does not refresh the cached counts (`frontend/src/features/datasets/dataset-queries.ts:76`, `:84`, `:114`, `:124`, `:134`, `:143`, `:153`, `:162`, `:172`, `:189`).
- The Interface lists `span-pick.ts:TraceRef` as a barrel export, but no feature outside `datasets` imports it (`frontend/src/features/datasets/index.ts:13`).

## frontend/src/features/scores/EGM.md

- The Overview says the failure-categories dialog is "used by Releases and review". Only `features/releases/failure-categories-card.tsx` imports `FailureCategoriesDialog`. `features/review` uses `FAILURE_CATEGORY_SCORE`, `DEFAULT_FAILURE_CATEGORIES` and `addCategoryProblem` in its own setup steps (`frontend/src/features/review/setup-categories-step.tsx`, `setup-steps.ts`, `setup-review-dialog.tsx`).
- The doc says `scores-panel.tsx:ScoresPanel` is used by `features/traces`. It is also used by `features/annotations` (`frontend/src/features/annotations/scores-panel.tsx`, `frontend/src/features/annotations/annotate-page.tsx`).
- The barrel exports `add-score-dialog.tsx:AddScoreDialog`, but no file outside `features/scores` imports it (`frontend/src/features/scores/index.ts:7`).
- Coverage: the doc does not list the external users of the barrel's shared hooks and constants. `useScoreConfigsQuery` is used by releases, experiments, datasets and annotations. `useTraceScoresQuery` and `useRefreshScores` are used by annotations (`features/annotations/last-item-strip.tsx`, `use-work-session.ts`). `FAILURE_CATEGORY_SCORE` is used by releases, annotations and review.

## frontend/src/features/exports/EGM.md

No drift detected.

## frontend/src/features/evaluators/EGM.md

- The invalidation invariant reads as if every evaluator write invalidates "on success or error". `useCreateEvaluator` and `useUpdateEvaluator` invalidate (and invalidate `prompts.all` for a templated judge) only in `onSuccess`. Only `useTestEvaluator` uses `onSettled` (`frontend/src/features/evaluators/evaluator-queries.ts:141`, `:150`, `:160`).
- The test-trace invariant says "at most 5 shown". It does not say that the query fetches 25 candidates (`TEST_TRACE_FETCH`) and filters them on the client, so fewer than 5 can show even when more matching traces exist (`evaluator-queries.ts:209-211`, `:217-242`).
- Coverage: the doc leaves out the page's other cross-feature imports, `features/auth:LiveDemoButton` and `features/scores:ScoreSettingsLinks` (`frontend/src/features/evaluators/evaluators-page.tsx:13-14`).

## frontend/src/features/alerts/EGM.md

- Interface lists `mute-presets.ts:PRESETS_BY_ID` as a pure helper, but it is a module-private constant (`frontend/src/features/alerts/mute-presets.ts:24`, no `export`). The exported surface is `MUTE_PRESETS`, `presetById`, `isMutePresetId`, `muteEnd`, `presetHint` and `DEFAULT_MUTE_PRESET` (`mute-presets.ts:32-55`).
- Invariants cite `rule-detail-header.tsx:RuleDetailHeader` as a page that shows a `ReadOnlyNote` without `alerts:write`. The header only hides its controls; the note is rendered by `rule-detail-page.tsx:73` (`AlertRuleDetailPage`), which the doc does not cite for this invariant.
- Invariants say email recipients "must be org members" as part of the bounds mirrored in `channel-schema.ts`. `channel-schema.ts:recipientProblem` (`channel-schema.ts:94-102`) and `kindIssues` (`channel-schema.ts:108-114`) check only address syntax and the count of 10; membership is offered by the picker (`recipient-field.tsx:45-52`) and enforced by the server, not by the schema.
- Side Effects says "Rules, the open rule and event lists poll every 30 s". `useAlertRuleQuery` polls only once the rule has loaded; a rule that never loaded is not polled (`alerts-queries.ts:33`). Not stated.
- Coverage: the legacy `/alerts/channels` project route, which redirects to `/$orgId/$projectId/settings/channels` (`frontend/src/app/router.tsx:711-718`), and the fact that `AlertChannelsPage` is mounted under the settings route (`router.tsx:813-821`) are not in the Interface section, which lists only the three lazy pages.
- Coverage: `use-rule-preview.ts` exports `PREVIEW_DEBOUNCE_MS` and the `RulePreviewState` union (`blocked`, `loading`, `paused`, `error`, `ready`; `use-rule-preview.ts:10-22`) that callers of `useRulePreview` switch on; the contract names the hook but not the state shape. Same for `channels-queries.ts:DELIVERY_PAGE_SIZE` (`channels-queries.ts:14`).

## frontend/src/features/annotations/EGM.md

- Interface says `index.ts` is the public surface (three pages only) and that `queue-format.ts:progressTotal` / `progressRatio` / `progressSummary` are "imported from outside the folder". Those outside imports bypass `index.ts`: `frontend/src/features/review/reason-progress.tsx:2` and `frontend/src/features/review/reason-filter.tsx:5` import `@/features/annotations/queue-format` directly, contradicting `index.ts:1-4` ("Import from this file, not from the modules behind it"). The contract should either list these helpers on `index.ts` or record the deep import as a known deviation.
- Invariants say `in-flight-claims.ts:claimFor` "keeps at most one claim per project and queue". The code adopts only an orphaned in-flight claim; when a non-orphaned claim is already on the wire, it sends a second `annotationsApi.claim` and overwrites the map entry (`in-flight-claims.ts:31-44`).
- Coverage: `annotation-queries.ts` re-exports `useScoreConfigsQuery` from `@/features/scores` (`annotation-queries.ts:17`); not listed among the queue reads.
- Coverage: `annotation-errors.ts` also exports `unknownTraceIds` and `workNotice` (`annotation-errors.ts:38`, `:56`), and `use-review-actions.ts` exports `VIEWER_REASON` (`use-review-actions.ts:9`); the contract lists only `queueErrorMessage` / `isClaimLost` and `useReviewActions`.
- Side Effects: `useMembersQuery` has a 5-minute `staleTime` and shares the `queryKeys.org(id).members` cache entry with the Members page (`annotation-queries.ts:63-69`); not stated.

## frontend/src/features/sessions/EGM.md

- Invariants say `session-summary.ts:sumTokens` "returns null when no turn reported tokens". It returns a `TokenTotals` object whose `input` and `output` are summed with `?? 0` and only `total` is null when nothing was reported (`frontend/src/features/sessions/session-summary.ts:94-111`). Callers that read `input` / `output` get 0, not null, for unknown usage.
- Invariants attribute the "+" marker on partial totals to `session-conversation.tsx:SessionConversation`. The "+" is rendered by `session-summary-card.tsx:65` and `session-header.tsx:92`, `:168`; `SessionConversation` only withholds turn numbers (`session-conversation.tsx:73`).
- Invariants say previews are gated on the Last turn column and a non-phone viewport "(`session-queries.ts:toSessionListQuery`)". `toSessionListQuery` only forwards a `preview` boolean (`session-queries.ts:18-38`); the column and `PHONE_QUERY` checks live in `sessions-page.tsx:56-58`.
- Interface lists `session-format.ts:dayLabel` / `formatClock` as imported outside the folder; the module also re-exports `formatDayTime` from `@/lib/clock-format` (`session-format.ts:9`), not mentioned.
- Coverage: `useSessionTurnsQuery` pages at 100 traces (`session-queries.ts:9`, `SESSION_TURNS_PAGE_SIZE`) and ignores the page's time range (`session-queries.ts:55-58`); neither is stated. `features/traces/read-cancellation.test.tsx:18` also imports `useSessionListQuery` / `useSessionTurnsQuery` directly from `session-queries.ts` (test-only consumer).

## frontend/src/features/billing-sources/EGM.md

- Interface's "Imported from outside the folder" list names only `reconciliation-format.ts:formatUsd` / `isZeroMoney`, but `frontend/src/features/attribution/cost-strip/cost-strip-data.ts:4-8` also imports `formatVariance` and `sumMoney` from `@/features/billing-sources/reconciliation-format`. All of these deep imports bypass `index.ts`, which the contract presents as the public surface; the deviation is not recorded.
- Side Effects attributes `billingSourcesApi.reconciliationCsvUrl` to `download-csv.ts` but the Interface never names the exported helper that wraps it, `download-csv.ts:csvDownload` (`download-csv.ts:10-22`), which returns `{ href, windowText }` for the `<a download>` link.
- Interface lists `reconciliation-table.ts:flattenDays` and `reconciliation-summary.ts:summaryTiles` but omits that `reconciliation-table.ts` re-exports `summaryTiles` and `REASONS` (`reconciliation-table.ts:406-407`), plus its exported `renderRules`, `pendingDayRanges` and `bypassDayRange` (`reconciliation-table.ts:138`, `:390`, `:402`) used by the report view.
- Coverage: `source-form.ts:isNotConfigured` / `notConfiguredCopy` (`source-form.ts:46-58`) implement the `409 NOT_CONFIGURED` invariant but are not named; the invariant also omits that the notice text is the server's own `detail`, falling back to `NOT_CONFIGURED_FALLBACK`.

## frontend/src/features/review/EGM.md

- Side Effects attributes `projectsApi.filters` to `review-queries.ts`, but the Interface omits the hook that makes the call, `review-queries.ts:useEnvironmentsQuery` (`frontend/src/features/review/review-queries.ts:38-46`; shares `queryKeys.project(id).filters` with a 5-minute `staleTime` and selects `environments`). `useInvalidateReview` (`review-queries.ts:52-63`), which implements the stated invalidation set, is also exported and not named.
- Invariants say `useSaveFromReview` "sends the whole metadata plus `review`". The hook sends whatever `input.metadata` it is given (`review-queries.ts:138-140`); the whole-metadata composition is done by `save-from-review.ts:reviewMetadata` (`save-from-review.ts:55`), which the contract does not name.
- Interface describes `reason-copy.ts:replaySpanOf` as "the first failed span". It returns the item's `reason_detail.first_error_span_id` only when that span is present in the trace, else the latest-started LLM span (`reason-copy.ts:91-101`); it does not search the trace for failed spans.
- The "at least 2 categories" rule is cited to the categories step, but it is enforced in `setup-review-dialog.tsx:99`; `setup-categories-step.tsx` only locks `none` (`setup-categories-step.tsx:52-54`).

## frontend/src/features/users/EGM.md

- Invariants say "Load more is disabled while rows are placeholder data". The page passes `hasNextPage: usersQuery.hasNextPage && !stale` (`frontend/src/features/users/users-page.tsx:49`, `:69`), so the control is hidden, not disabled, while placeholder rows show.
- Side Effects omits the live polling source: `useUserListQuery` takes its `refetchInterval` from `useLiveProject` (`@/lib/live/use-live-project`, `users-queries.ts:17`), which the Invariants mention only as "live polling" without naming the dependency. `useUserDetailQuery` does not poll (`users-queries.ts:31-40`).
- Coverage: `user-sessions.tsx:8` deep-imports `dayLabel` and `formatClock` from `@/features/sessions/session-format` (the sessions folder has no `index.ts`); this cross-feature dependency is not recorded.
- Coverage: `computed-at.ts` exports the freshness copy `COMPUTED_AT_EXPLANATION`, `NOT_COMPUTED_EXPLANATION` and `LIVE_EXPLANATION` (`computed-at.ts:7-15`) that `ComputedAtChip` relies on; `user-format.ts` exports `NO_USER_PRICE` / `NO_USER_USAGE` reasons and `hasDailyCalls` (`user-format.ts:89-94`). None are listed.

## frontend/src/features/attribution/EGM.md

- Invariants describe `search.ts:attributionSearchSchema` (key pattern, `group_by` and `sort` defaults) but omit the `focus` rule: it is coerced to a string of 1-512 characters because the router reads an all-digit value as a number, and dropped on any invalid value (`frontend/src/features/attribution/search.ts:22-23`).
- Interface says `CostStrip` is reached through `index.ts`, but other features import the subfolder directly: `features/billing-sources/reconciliation-tab-page.tsx:10`, `source-summary.ts:6`, `source-summary-table.tsx:13` and `features/pricing/pricing-page.tsx:5` use `@/features/attribution/cost-strip` (including `useMappedSourceReports`, which `index.ts` does not re-export). The parent contract does not record that the subfolder is a second public surface.
- Side Effects says the chart or table choice is stored in `localStorage` from `spend-chart.tsx`; the storage access is in the shared hook `useChartView` (`frontend/src/lib/use-stored-choice.ts:67`), called at `spend-chart.tsx:35`. Not wrong, but the dependency is not named.

## frontend/src/features/attribution/cost-strip/EGM.md

- Invariants say a 403 or 404 on the sources list is `forbidden` "(card hidden)". The cards are not hidden: `billingCards` renders both the Provider billed and Variance cards with value null and the reason "You can't see this organization's billing sources." (`frontend/src/features/attribution/cost-strip/cost-strip-data.ts:358-366`, `BILLING_REASONS.forbidden` at `:210`).
- Interface says `index.ts` is the public surface, then lists `useCostStrip`, `reportWindow`, `costStripCards`, `tokenShares` and `TokenMixPanel` without noting they are not exported from `index.ts` (`cost-strip/index.ts:2-11` exports only `CostStrip`, `aggregateSources`, `BILLING_REASONS`, `TONE_ICONS`, `TONE_LABELS`, `varianceReason`, `SourceAggregate`, `useMappedSourceReports`).
- Coverage: the folder deep-imports `todayUtc` / `windowFromDays` from `@/features/billing-sources/window-picker` (`use-cost-strip.ts:3`) and `formatUsd` / `formatVariance` / `sumMoney` / `shortDay` from billing-sources modules (`cost-strip-data.ts:4-9`), and `varianceTone` from `@/features/traces` (`cost-strip-data.ts:10`); the reverse dependency on billing-sources is not recorded, though billing-sources also imports this folder (a cycle between the two features).
- Coverage: `useMappedSourceReports` also reads the sources list under `queryKeys.org(orgId).billingSources` (`use-cost-strip.ts:27-31`), the same key as `billing-sources-queries.ts:useBillingSourcesQuery` but with a 5-minute `staleTime`; the shared list key is not stated (only the report key is).

## frontend/src/features/budgets/EGM.md

- Invariants cite `budget-enforcement.ts:blockingBadge` for "'Blocking' shows only for `block` budgets in the firing state". `blockingBadge` only picks the label and tooltip by scope (`frontend/src/features/budgets/budget-enforcement.ts:40-51`); the firing and `block` gate is `budget-period.ts:isBlocking` (`budget-period.ts:97-99`), applied in `budget-row.tsx:40`. `isBlocking` is not named in the contract.
- Interface lists `budget-period.ts:formatUsd` as imported from outside the folder without noting that the import bypasses `index.ts` (`frontend/src/features/playground/replay-state.ts:1`), while `index.ts:1-4` says other features must import from `index.ts` only.
- Coverage: `budgets-queries.ts` re-exports `useAlertChannelsQuery` (from `@/features/alerts`) and `useGatewayKeysQuery` (from `@/features/gateway`) (`budgets-queries.ts:6-7`); the contract describes them only as reads. `useScopeSuggestions` reads `useProjectQuery` and `useFilterOptionsQuery` from the shell (`budgets-queries.ts:38-57`), which the Side Effects summarise as "shell project and filter queries" without naming them.
- Coverage: `budget-period.ts:inFlight` and `IN_FLIGHT_UNKNOWN_REASON` (`budget-period.ts:77-93`) render the in-flight reserve of `block` budgets as "—" with a reason when unknown; this unknown-value path is not in the Invariants.
- `burn-series.ts:burnForecast` returns null whenever the period has a single day (`burn-series.ts:66-69`), not by checking `period === "daily"`; the doc's "A daily budget has no forecast" is the consequence, not the rule.

## frontend/src/features/prompts/EGM.md

- Invariants attribute both Save gates to `draft.ts:hasChanges`. `hasChanges` only compares the template and advice with the base version (`frontend/src/features/prompts/draft.ts:147-152`); the "template has a problem" gate comes from `inspection.problem` in `prompt-editor.tsx:93-95`.
- Coverage: `template-cases.json` in this folder is the shared fixture that `frontend/src/lib/prompt-template.ts:3` (and, per its comment, the server and SDK) are checked against. The contract does not list the file or the obligation that changes to template rendering keep it in sync.
- Coverage: `prompt-errors.ts` also exports `isProblem`, `PROMPT_IN_USE_MESSAGE`, `SAVE_DISABLED_HINT` and `LATEST_RESERVED_REST` (`prompt-errors.ts:5-40`), and `use-prompt-links.ts` exports `usePromptLink` / `usePromptName` / `useDiffLink` (`use-prompt-links.ts:13-28`); none are listed.
- Side Effects omit the stale times that shape refetching: `useVersionUsageQuery` 60 s, `useAuthorsQuery` 5 min on the shared `queryKeys.org(id).members` entry (`prompt-queries.ts:61-78`), picker queries 60 s and `promptVersionByIdQuery` 5 min (`prompt-query-options.ts:16-40`).

## frontend/src/features/legal/EGM.md

- Coverage: the legal route paths are not owned by this folder. `frontend/src/app/router.tsx:24` imports `LEGAL_PATHS` from `frontend/src/features/landing/links.ts:62`, and every legal route uses it (`router.tsx:99-138`); the contract's Invariants describe the route gate but not where the paths live.
- Coverage: `legal-page.tsx` also depends on `LandingNav` and `LandingFooter` from `features/landing` (`legal-page.tsx:3-4`), not only `useStartDemo`; a change to those components changes every legal page.
- `LEGAL_FACTS.supervisoryAuthority` is typed `{ name, url } | null` (`legal-facts.ts:21`), but no page renders `url`; only `.name` goes through `Fact` (`content/privacy.tsx:287-290`). The contract lists the fact without noting the unused field.

## frontend/src/features/legal/content/EGM.md

- Side Effects says the pages render "static text, mailto links and router links". `terms.tsx` also renders external documentation links from `DOCS_LINKS` (`frontend/src/features/legal/content/terms.tsx:3`), which the Interface and Side Effects do not mention.
- Invariants say nullable facts, including `supervisoryAuthority`, always render through `Fact`. Only `supervisoryAuthority?.name` is rendered (`privacy.tsx:287-290`); the authority's `url` is never shown, so a confirmed authority would appear without its link.
- `subprocessors.tsx` has no `SECTIONS` map or contents list (`subprocessors.tsx:14`), and neither does `imprint.tsx` (`imprint.tsx:10`); the contract correctly limits the `SECTIONS` rule to four files but does not say the other two pass no `contents`, which affects the "more than three sections" rule in `LegalPage` (`legal-page.tsx:11`). Minor.

## frontend/src/features/overview/EGM.md

- Invariants say `page-state.ts:statusFor` "returns no hero status while key figures are unknown". It bails out only when `traces` or `llm_calls` is null or the aggregate is unavailable (`frontend/src/features/overview/page-state.ts:28-34`); when `error_rate` is null it still builds a status with `errors: errorCountFrom(...) ?? 0` (`page-state.ts:52`), so an unknown error count is passed to `heroStatus` as 0. Either the code or the invariant is wrong; under the project's real-data rule this is a code-side gap.
- Side Effects list the live-polled queries as overview, timeseries, models and health, but `useTracesTabQuery` also polls through `useLiveProject` while the range is not custom (`use-overview-queries.ts:217`, `:234`). The month-to-date overview query does not poll (`use-overview-queries.ts:84-88`); neither detail is stated.
- Coverage: `needs-attention.ts:attentionItems` drops disabled rules and disabled budgets (`needs-attention.ts:63`, `:77`), and budgets at 100 % or more are `critical` (`needs-attention.ts:87`); the contract lists ordering and caps only.
- Coverage: `release-markers.ts:windowMarkers` also returns no markers when `releases` is undefined, and the 8-marker cap and newest-first order are applied in `markersInWindow` (`release-markers.ts:11`, `:23-39`), which the contract does not name.

## frontend/src/features/pricing/EGM.md

- Coverage: cross-feature deep imports are not recorded. `pricing-page.tsx:5` imports `CostStrip` from `@/features/attribution/cost-strip`, `price-override-dialog.tsx:12` imports `todayUtc` and `overrides-card.tsx:10` imports `shortDay` from `@/features/billing-sources/window-picker`, bypassing those features' `index.ts` files.
- Coverage: `price-override-form.ts` exports `emptyOverrideValues` (`price-override-form.ts:80-96`), which fixes the default `effective_from` to today's UTC day and applies the URL prefill; and `PROVIDER_MAX_LENGTH` / `PATTERN_MAX_LENGTH` (`:22-23`). Not listed in the Interface.
- Side Effects omit that `usePricesQuery` has a 10-minute `staleTime` and is not project-scoped (`queryKeys.prices`, `pricing-queries.ts:7-13`), and that `useUnpricedModelsQuery` keeps previous data while the window changes (`pricing-queries.ts:16-25`).
- Invariant "rates are decimal strings below 1,000,000 with at most 6 decimals" omits that the client also accepts trailing or leading dots ("1.", ".2") and normalises them before sending (`price-override-form.ts:24`, `:99-101`).

## frontend/src/features/onboarding/EGM.md

- Interface lists `activation-queries.ts:useActivationQuery` but not its sibling export `useChecklistGate` (`frontend/src/features/onboarding/activation-queries.ts:11-19`), which supplies `hasTraces`, `isDemoOrg` and `dismissed` to `checklistVisible`; nor `test-trace-button.tsx:TestTraceButton` (`test-trace-button.tsx:27`), the component that calls `sendTestTrace`.
- Invariants say key creation needs `key:create` and project creation `project:write`. These are checked with `can(membership.role, …)` against the URL's membership (`connect-step.tsx:73`, `project-step.tsx:42`), not with `usePermission` like the rest of the app; the contract does not say which role source is used.
- `useOnboardingStatus` fetches `projectsApi.activation` on every 2 s poll alongside `projectsApi.onboarding` (`use-onboarding-status.ts:19-24`), and `activation` is called without the abort `signal`; the Side Effects list both calls but not that each poll makes two requests.
- Side Effects says the last project is read "from browser storage"; the read goes through `readLastProject` in `frontend/src/lib/last-project.ts` (`onboarding-page.tsx:12`, `:201`), which the contract does not name.

## frontend/src/features/gateway/EGM.md

- Interface lists `overview-format.ts:FAILING_SCENARIOS` alongside exported helpers, but it is a module-private constant (`frontend/src/features/gateway/overview-format.ts:56`, no `export`); callers reach it only through `labErrorCount` (`overview-format.ts:66-70`), which the contract does not name.
- Invariants say `gatewayWindow` falls back only when the window is longer than 7 days. It also falls back whenever the range value is not one of the gateway picker's presets, which includes custom ranges of any length (`overview-format.ts:27-32`).
- Coverage: `environment.ts:environmentTone` / `EnvironmentTone` (`environment.ts:23-37`) and `gateway-url.ts:GATEWAY_HOST_PLACEHOLDER` (`gateway-url.ts:17`) are exported and used by the subfolders but not listed; `overview-format.ts` also exports `NO_REQUESTS_REASON`, `formatErrorShare`, `formatMs`, `sortedFaults`, `providerLabel` (`overview-format.ts:17-85`).
- Side Effects omit that the overview query keeps previous data while the window changes (`overview-queries.ts:20-25`) and that `GatewayBaseUrl` reads `useMe().capabilities.gateway_mode` (`gateway-base-url.tsx:17`).

## frontend/src/features/gateway/lab/EGM.md

- Coverage: `profile-errors.ts` is not mentioned. It exports `PROFILE_FIELD_OF_API_FIELD`, `profileParamErrors`, `keyFailureMessage` and `isProductionKeyFailure` (`frontend/src/features/gateway/lab/profile-errors.ts:6-36`), which map server field errors to the form and turn per-key attach failures (including `FAULT_PROFILE_ON_PRODUCTION_KEY`) into messages; the contract's key-failure invariant does not name them.
- Coverage: `useLabRunsQuery` stores its runs under `queryKeys.project(projectId).traces(query)` (`lab-queries.ts:60`), the traces list's key family, so the claim "trace queries are not invalidated" holds for Lab mutations but those entries are shared with, and refreshed by, the traces feature. The window also ends one minute after mount (`lab-queries.ts:45`), not at mount.
- Coverage: `scenario-params.ts` exports `SCENARIOS`, `PROVIDER_5XX_STATUSES`, `UNSUPPORTED_PARAM_PATTERN`, `defaultParamText`, `storedParamText`, `formatProbability` and `parsePercentText` (`scenario-params.ts:12-284`), not listed; the `unsupported_parameter` rule (`param` 1-64 characters matching `UNSUPPORTED_PARAM_PATTERN`, `scenario-params.ts:57-59`) is absent from the mirrored-bounds list.

## frontend/src/features/gateway/keys/EGM.md

- Invariants say "Create is disabled when the project has no routes". The page's Create key button is always enabled for writers (`frontend/src/features/gateway/keys/keys-page.tsx:116-136`); only the dialog's submit is disabled (`create-key-form.tsx:82`) and the route field explains why (`route-field.tsx:23-33`, `:95`).
- Interface describes `key-form.ts:widens` as "true when an edit lets the key spend more". It also returns true for any change of `route_id` or `environment`, regardless of spend (`key-form.ts:265-267`), so re-authentication is required for those edits too.
- Interface describes `key-snippets.ts:sdkSnippet` as "Python setup snippet". It takes an `SdkChoice` (`"openai" | "anthropic"`) and the gateway mode, returns an OpenAI or Anthropic client snippet with the matching base URL, and substitutes a placeholder host when this origin does not serve the gateway (`key-snippets.ts:5-27`).
- Coverage: `useUpdateGatewayKey` sets `gcTime: 0` (`keys-queries.ts:51`) and, unlike revoke and purge, leaves error reporting to the caller (`keys-queries.ts:46`); not stated. `key-form.ts` also exports `parseList`, `parseLimit`, `modelNameProblem`, `tagProblem`, `cacheLabel`, `FAULT_ON_PRODUCTION_REASON`, `DETACH_BEFORE_PRODUCTION` and `detachToSaveMessage` (`key-form.ts:29-205`), none listed.
- Coverage: the folder depends on `useMemberReauth` from `@/features/settings/use-member-reauth` (`create-key-form.tsx:9`, `edit-key-sheet.tsx:9`), a deep cross-feature import the contract mentions only by hook name.

## frontend/src/features/gateway/routes/EGM.md

- Invariants say that without `gateway:write` "the fieldset is disabled and a `ReadOnlyNote` shows". That holds for the list and an existing route (`routes-page.tsx:64`, `route-editor-existing.tsx:104`, `route-editor-form.tsx:39`), but `NewRouteEditor` renders `NoAccess` instead (`frontend/src/features/gateway/routes/route-editor-new.tsx:39-41`).
- Invariants say missing credential names render through `UnknownValue` as "Unknown credential". They are plain text: `version-diff.ts:39` builds the string "Unknown credential <id prefix>" for the diff, and `credential-select.tsx:53` renders a `SelectItem` labelled "Unknown credential"; neither uses `UnknownValue` or carries a reason tooltip.
- Coverage: `use-route-form.ts:useRouteForm` (`use-route-form.ts:10`) and `use-route-lookups.ts:useRouteLookups` / `RouteLookups` (`use-route-lookups.ts:10-27`), which the editors use for form setup and credential/key name lookups, are not listed. `route-form.ts:newRouteFormValues`, `DEFAULT_RETRY_STATUSES`, `DEFAULT_FALLBACK_CONDITIONS`, `aliasRowsFromMap` / `aliasMapFromRows` and `errorPaths` (`route-form.ts:33-281`) and `version-diff.ts:versionSummary` (`version-diff.ts:116`) are also exported and unlisted.
- `useRouteSave` `discard` resets to the route prop's version (`use-route-save.ts:68-71`), which can be older than a version fetched by `reloadLatest`; the contract notes only that `discard` keeps `conflictVersion`.

## frontend/src/features/gateway/credentials/EGM.md

- Invariants cite `credential-row.tsx:CredentialRow` as showing a `ReadOnlyNote` to members without `credentials:manage`. The row only hides its actions when `canManage` is false (`frontend/src/features/gateway/credentials/credential-row.tsx:123`); the `ReadOnlyNote` is rendered once by `credentials-page.tsx:77`.
- Interface says `credential-model.ts:baseUrlLabel` is "imported outside the folder" without naming the consumer or noting the bypass of `../index.ts`: `frontend/src/features/playground/credential-options.ts:45` deep-imports it.
- Side Effects list only `useCredentialsQuery` as a read, but the page also reads `useGatewayRoutesQuery` (`credentials-page.tsx:35`) to build the per-credential route usage that gates Delete; the "failed routes query falls back to an empty list" invariant depends on that read.
- Coverage: `credentials-queries.ts` re-exports `useCredentialsQuery` (`credentials-queries.ts:7`); `credential-model.ts` exports `usedByLabel`, `routeNamedIn` and `NOT_CONFIGURED_COPY` (`credential-model.ts:64-101`); `credential-form.ts` exports `needsBaseUrl`, `isHttpsUrl`, `PROVIDER_LABELS`, `PROVIDER_HINTS` and `CREDENTIAL_FIELD_OF_API_FIELD` (`credential-form.ts:13-90`). None are listed. `credential-form.ts:apiKeySchema`, cited in the Invariants, is module-private (`credential-form.ts:30`).

## frontend/src/app/EGM.md

- The doc lists `/alerts/channels` among the project child pages. In the code it is a redirect-only route with no component: its `beforeLoad` throws `redirect({ to: "/$orgId/$projectId/settings/channels", params, search })` (`frontend/src/app/router.tsx:710-717`). The doc should say it is a legacy redirect.
- Coverage: the doc omits the project route `/settings/billing-sources/$sourceId` (`reconciliationRoute`, `ReconciliationPage`, search validated by `reconciliationSearchSchema`). It is a direct child of `projectRoute`, a sibling of the settings layout rather than a child of it (`frontend/src/app/router.tsx:834-847`, registered at `:949`).
- Coverage: the doc omits the two index redirects. `/$orgId/$projectId/` redirects to `/overview` (`projectIndexRoute`, `frontend/src/app/router.tsx:353-359`), and `/settings/` redirects to `/settings/project` (`settingsIndexRoute`, `frontend/src/app/router.tsx:765-771`). The `/settings` layout itself (`SettingsLayout`, `:756-763`) is also not named.
- Coverage: the Invariants section describes guards for `/`, `/login`, `/signup`, `/login/two-factor` and `_authed` only. The `/accept-terms` guard is not described: it sends a signed-out user to `/login?next=<href>` and, when no terms are pending, redirects to `safeNextPath(search.next) ?? "/"` (`frontend/src/app/router.tsx:273-281`). `/invite` puts `me` (possibly null) into the route context through its `beforeLoad` (`:251`), and `/invite/$token` replaces itself with `/invite#token=<token>` (`:260-262`); neither is described.
- "Every route sets the tab title to `<Page> · Spanlight` through `pageTitle`" is not exact. The landing route sets the literal title "Spanlight — Open-source LLM observability" instead of using `pageTitle` (`frontend/src/app/router.tsx:85-87`), and the layout and redirect-only routes (`_authed`, `/$orgId/$projectId`, `/settings`, the index redirects, `/alerts/channels`, `/invite/$token`) set no title.

## frontend/src/components/EGM.md

- The doc lists `agreement-format.ts` exports as including `MIN`. No such export exists; the constant is `MIN_PAIRS` (`frontend/src/components/agreement-format.ts:6`).
- `error-state.tsx:ErrorState` is described as showing `errorMessage(error)`. It shows `description ?? errorMessage(error)`, so a caller-supplied `description` replaces the error's message (`frontend/src/components/error-state.tsx:58`). The `retrying` (spinner and disabled retry button) and `compact` props are also not mentioned (`:10-17`, `:64`).
- `confirm-dialog.tsx:ConfirmDialog` focus handling is only half described. Besides moving focus to the enclosing `[role="region"]` card when the row is gone, in controlled (`control`) mode it returns focus to the menu's trigger button (found through `aria-controls`) rather than the vanished menu item (`frontend/src/components/confirm-dialog.tsx:91-100`, `:142-150`).
- `theme-provider.tsx:ThemeProvider` is said to fall back to `DEFAULT_THEME`; the doc does not state that this default is `light`, not `system` (`frontend/src/lib/theme.ts:11`), so with nothing stored the app ignores `prefers-color-scheme`. Callers read the theme through `useTheme` in `src/lib/theme.ts` (`:21-27`), which this doc does not point to.
- The doc says public routes (landing and auth) must not import chart code statically, "which `frontend/scripts/check-public-bundle.mjs` checks". The script checks only the landing page, forgot-password, invite, reset-password, verify-email and the shared login/signup entry (`frontend/scripts/check-public-bundle.mjs:8-15`, `:19-21`); the other public pages (two-factor, confirm-email-change, unsubscribe, accept-terms and the legal pages) are not covered.
- Coverage: `status-dot.tsx` also exports the type `StatusDotState` (`frontend/src/components/status-dot.tsx:67`), and `chip-multi-select.tsx` re-exports `ChipOption` (`frontend/src/components/chip-multi-select.tsx:9`); neither is listed.

## frontend/src/components/ui/EGM.md

- `button.tsx:Button` with `asChild` drops `loading` and `disabled`: both are destructured and never passed to `Slot.Root`, so no spinner, no `aria-busy` and no disabled state are rendered in that mode (`frontend/src/components/ui/button.tsx:66-82`). The doc presents `loading` as a general prop and says a loading button "is disabled and sets `aria-busy`" without this exception.
- `sheet.tsx:SheetContent` also accepts `hideClose` (`frontend/src/components/ui/sheet.tsx:16`, `:25`, `:46`); the doc lists only `side`.
- `dialog.tsx:DialogContent` with `returnFocus`: a caller-supplied `onCloseAutoFocus` overrides the return-focus behaviour, because `{...props}` is spread after it (`frontend/src/components/ui/dialog.tsx:47-61`). The doc does not state this precedence.

## frontend/src/components/data-table/EGM.md

- The doc says `DataTable` sets `aria-busy` while loading with rows already shown. It sets `aria-busy` whenever `loading` is true, including the skeleton state with no rows; only the dimming (`opacity-60`) is limited to loading with rows (`frontend/src/components/data-table/data-table.tsx:118`, `:131`).
- The doc says `DataTableBody` keeps the focused row mounted while scrolled out of view. Focus tracking only runs for a virtualized body (more than `VIRTUALIZE_ROWS` rows); the `onFocus`/`onBlur` handlers are not attached otherwise (`frontend/src/components/data-table/data-table-body.tsx:36`, `:99-107`). This is consistent in effect but the doc should scope it to the virtual body.
- Coverage: `use-table-prefs.ts` exports `ROW_PX` (`{ comfortable: 40, compact: 32 }`) and `HEADER_PX` (`36`), the virtualizer stride and header height (`frontend/src/components/data-table/use-table-prefs.ts:8-10`); the doc does not mention them.

## frontend/src/lib/EGM.md

- The doc says the `format.ts` formatters return `null` for non-finite input. `formatCost` only checks finiteness for string input (through `parseMoney`); a numeric `NaN` or `Infinity` passes straight through (`typeof value === "number" ? value : parseMoney(value)`) and is rendered as `$NaN` or `$∞` instead of `null` (`frontend/src/lib/format.ts:76-91`).
- Coverage: `reveal-in-scroller.ts` (`revealInScroller`, `horizontalReveal`, `frontend/src/lib/reveal-in-scroller.ts:10`, `:21`) is not mentioned anywhere in the doc. It is a DOM helper that scrolls a horizontal scroller to reveal an item.
- Coverage: `clock-format.ts` also exports `parseDate` and `isSameDay` (`frontend/src/lib/clock-format.ts:28`, `:34`); the doc lists only `formatClock`, `formatDayTime` and `dayLabel`.
- Coverage: `retry-reasons.ts` is listed as a label map without naming its exports, which are two unknown-value reason strings, `RETRY_NOT_COMPUTED_REASON` and `RETRY_NO_KNOWN_COST_REASON` (`frontend/src/lib/retry-reasons.ts:7-8`), not a label map.
- Coverage: `score-format.ts` also exports the limits `VALUE_LIMIT`, `COMMENT_LIMIT` and `CATEGORY_LIMIT` and the `ScoreInputCheck` type (`frontend/src/lib/score-format.ts:9-11`, `:92`); `prompt-template.ts` also exports `TEMPLATE_TOO_LARGE_MESSAGE` (`frontend/src/lib/prompt-template.ts:18`). None are listed.

## frontend/src/lib/live/EGM.md

No drift detected.

## frontend/src/lib/api/EGM.md

- The doc says all type modules, including `attribution-types.ts`, "are re-exported through `types.ts` and `index.ts`". `types.ts` re-exports only seven of them; `attribution-types.ts` is re-exported by `index.ts` alone (`frontend/src/lib/api/types.ts:44-50`, `frontend/src/lib/api/index.ts:6`).
- The doc says to import through `index.ts`, and lists `paths.ts:orgPath`/`projectPath`, `client.ts:filenameFromDisposition`, `client.ts:QueryParams` and `errors.ts:parseRetryAfter` as interface. None of these are re-exported from `index.ts` (`frontend/src/lib/api/index.ts:13`, `:15-22`); callers such as `src/lib/live/live-provider.tsx:14` import `@/lib/api/paths` directly.
- Path encoding: the doc names end-user ids and prompt names as the values that get a leading `~`. Session ids also get it on the wire: `projectsApi.sessionTurns` sends `encodeURIComponent(toPathId(sessionId))` (`frontend/src/lib/api/projects.ts:226`). The comment in `end-users.ts` claims session ids are escaped "in the app route only" (`frontend/src/lib/api/end-users.ts:5-9`), which contradicts `projects.ts`; the doc should state the actual wire behaviour.
- Query keys: the doc says "every key starts with a stable scope (`["org", id]` or `["project", id]`)". The top-level keys it lists in the same doc do not: `["me"]`, `["auth", …]`, `["prices"]` and `["invite", "preview", token]` (`frontend/src/lib/api/query-keys.ts:38-46`).
- Domain table, `promptsApi`: "it also looks up a version by id" omits the route, which is outside the `/prompts` root: `GET /projects/{id}/prompt-versions/{versionId}` (`frontend/src/lib/api/prompts.ts:186-189`). The single-version read `GET /prompts/{name}/versions/{v}` (`:165`) is also not listed.
- Domain table, `billingSourcesApi`: the module also builds a non-fetch URL for `/orgs/{id}/billing-sources/{s}/reconciliation.csv` through `buildUrl` (`frontend/src/lib/api/billing-sources.ts:252-256`); the doc does not mention it.
- Domain table, `alertsApi`: the routes under the roots that cause side effects are not listed: `/alert-channels/{c}/test`, `/alert-channels/{c}/deliveries` and `/deliveries/{d}/retry`, `/alert-rules/{r}/mute`, `/alert-rules/preview` and `/alert-events/{e}/acknowledge` (`frontend/src/lib/api/alerts.ts:72`, `:83`, `:87`, `:104`, `:107`, `:114`). The delivery retry re-sends a notification but is missing from the Side Effects list.
- Domain table, `authApi`/`securityApi`: the sub-routes `/auth/terms/accept` (`frontend/src/lib/api/auth.ts:47`), `GET /auth/totp` (`frontend/src/lib/api/security.ts:42`) and `POST /auth/oauth/{provider}/link` (`frontend/src/lib/api/security.ts:69`) are not shown; `securityApi` is listed with `/auth/oauth/{provider}` only, which is the `DELETE` unlink route.
- `client.ts:download` always sends `Accept: text/csv` (`frontend/src/lib/api/client.ts:166`), including for `orgsApi.downloadAudit` with `format = "jsonl"` (`frontend/src/lib/api/orgs.ts:132`). The doc describes `download` without this fixed `Accept` value.
- `getAggregateList` does not follow the "a `204` or empty body resolves to `undefined`" rule: it calls `response.json()` directly and would throw on an empty body (`frontend/src/lib/api/client.ts:229`). It also always sets `oldest_pending_at`, to `null` when the header is absent (`:236`).
- `errors.ts:ExportTooLargeError` rebuilds the error from selected fields and drops `extensions`, `retryAfterSeconds`, `currentVersion` and `runError` of the source error (`frontend/src/lib/api/errors.ts:64-74`); the doc presents it as a plain conversion.

## frontend/public/EGM.md

- The doc says the folder holds one script plus the favicon and the landing hero media. It also holds `.well-known/security.txt` (`frontend/public/.well-known/security.txt:1-5`), served at `/.well-known/security.txt`. That URL is checked by `deploy/smoke.sh:334-336` and its `Expires` field by `frontend/src/features/legal/security-txt.test.ts`, so renaming or letting it lapse breaks those checks. The doc's file list, Interface and the "file names here are public URLs" invariant omit it.

## frontend/scripts/EGM.md

- The overview says the script "checks that public, signed-out routes do not load chart code". It checks only a fixed subset: `index.html`, the landing page, forgot-password, invite, reset-password, verify-email and the shared `_auth-*.js` login/signup entry (`frontend/scripts/check-public-bundle.mjs:8-21`). The public routes `/login/two-factor` (`two-factor-page`), `/confirm-email-change`, `/unsubscribe`, `/accept-terms` and the legal pages (`frontend/src/app/router.tsx:96-146`, `:177-288`) are not checked. The interface list itself is accurate, but the overview overstates the coverage.

## sdks/EGM.md

- "Unknown is not zero" says a stream cut before final usage "sends `output_tokens` as absent and sets `usage.unreported_reason`" and cites `_usage.py:clear_output_tokens` and `span.ts:Span.clearOutputTokens`. Those methods only clear the output count (`sdks/python/src/spanlight/_usage.py:109-117`, `sdks/typescript/src/span.ts:197-199`). The reason attribute is set by the stream accumulators: `sdks/python/src/spanlight/integrations/_openai_stream.py:113-114,189-190`, `_anthropic_stream.py:128-130`, `sdks/typescript/src/providers/openai.ts:214,326-330`, `providers/anthropic.ts:183-184`. The citation should point at the accumulators for the reason half.
- "Shared rule tables" says service tiers are "verbatim copies of backend rules" and lists `typescript/src/tiers.ts`. The TypeScript copy is reduced: it keeps only the OpenAI and Anthropic spellings and aliases and drops the Google alias set (`sdks/typescript/src/tiers.ts:1-5,16-20`). The Python copy keeps `_GOOGLE_ALIASES` (`sdks/python/src/spanlight/_tiers.py:26-48`). For the providers the fixture covers, the results are the same, but the copy is not verbatim.
- "Environment" lists the variables both SDKs read, plus `SPANLIGHT_TOKEN` for the CLI. It omits `SPANLIGHT_PROJECT_ID`, which the Python eval-gate CLI reads (`sdks/python/src/spanlight/_experiments.py:167,199`). It also omits `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT` (`_experiments_runs.py:68`, `_experiments.py:185-191`).

## sdks/python/EGM.md

- The doc says "The SDK tags the spans it sends with `sdk: spanlight-python`." Spans carry no `sdk` field (`sdks/python/src/spanlight/_span.py:239-268`). The value goes in the batch-level `client` counters object sent beside the spans (`sdks/python/src/spanlight/_exporter.py:131-144`), and in the `User-Agent: spanlight-python/<version>` header (`_exporter_delivery.py:75`).

## sdks/python/examples/EGM.md

No drift detected

## sdks/python/src/EGM.md

No drift detected

## sdks/python/src/spanlight/EGM.md

- "Exporter creation" says that without an enabled client, an API key and a safe host, "tracing does nothing and logs a warning". When `enabled=False` (argument or `SPANLIGHT_ENABLED`), no warning is logged. The warnings fire only for an enabled client with no key or an unsafe host (`sdks/python/src/spanlight/_client.py:119-129`). The doc also leaves out that prompts are still fetched while tracing is disabled, as long as a key and a safe host are set (`_client.py:60-61,110-116`).
- The Interface lists `config` and `prompts` as properties of `Spanlight`. They are plain instance attributes set in `__init__` (`_client.py:92,110`). Only `enabled` and `exporter` are properties (`_client.py:149-157`).
- "Deliberate exceptions" cites `_experiments.py:ExperimentError`. The class is defined in `_experiments_api.py:27`. `_experiments.py` only imports it and re-exports it in its `__all__` (`_experiments.py:26-28,47`).
- "Deliberate exceptions" says `PromptUnavailableError` is raised when nothing is cached, the fetch failed and no valid fallback exists. It is also raised, before any request, for an empty name or a name with an empty, `.` or `..` segment (`_prompt_client.py:113-114`). The doc lists that check under "Prompt cache" but does not say which exception it raises.

## sdks/python/src/spanlight/integrations/EGM.md

- The doc says "`_provider_errors.py:record_response_details` reads only `error.response` headers and status." It also reads `error.body`, `error.type` and `error.code` to fill `provider.error.type` and `provider.error.code` (`sdks/python/src/spanlight/integrations/_provider_errors.py:87,175-188`). The module's guarantee is narrower: it never reads `error.request` (`_provider_errors.py:73-74`).

## sdks/typescript/EGM.md

No drift detected

## sdks/typescript/src/EGM.md

- "Shared rule" says "`tiers.ts:normalizeTier` copies the server's tier table". It is a partial copy: only the OpenAI and Anthropic spellings, with no Google aliases (`sdks/typescript/src/tiers.ts:1-5,16-40`). Compare the server and Python table in `sdks/python/src/spanlight/_tiers.py:26-48`.
- Coverage: "Token convention" says only that counts which are not non-negative safe integers are ignored (`span.ts:299-301`). It does not say that, unlike Python, no upper bound is enforced. Python drops counts above 2,000,000,000 tokens or 100,000 searches, because the server rejects the whole span (`sdks/python/src/spanlight/_usage.py:16-18,140-146`). In TypeScript, a count above that limit goes through and makes the server reject the span.

## sdks/typescript/src/providers/EGM.md

- "These are the same rules as the Python SDK" (pricing modifier) is not exact. Python also reads `inference_geo`, `speed` and `web_search_options` from `extra_body` when the top-level value is missing or a sentinel (`sdks/python/src/spanlight/integrations/_modifiers.py:56-66`). TypeScript reads only top-level body fields (`sdks/typescript/src/providers/common.ts:110-115`, `providers/openai.ts:102-105`). Python also flags OpenAI regional hosts with `usage.pricing_modifier_unknown=regional_endpoint` (`_modifiers.py:69-80`, `integrations/openai.py:93`). TypeScript has no equivalent.
- The Overview says "Reading responses follows the Python SDK's `integrations/` modules." Several parts of the Python behaviour are absent, and the doc does not say so:
  - `finishWithError` records only `http.status_code` and `provider.request_id` (`common.ts:201-212`). The provider error type and code, `provider.ratelimit`, `provider.retry_after_s` and `spanlight.gateway.error_code` are missing. Compare `_provider_errors.py:70-89`.
  - No `request_hash` is set. Compare `_common.py:191`.
  - Anthropic `usage.inference_geo` and `usage.speed` attributes are not recorded (`anthropic.ts:67-70`). Compare `_anthropic_message.py:20-36`.
  - `wrapAnthropic` patches only `client.messages.create` (`anthropic.ts:32-39`). It does not patch `client.beta.messages` or `messages.batches.results`.
  - Clients from `copy()` and `withOptions()` are not instrumented. Compare `_common.py:247-259`.
- The token convention says `anthropic.ts:usageFields` drops the 1-hour cache-write count "when that count is above the cache-write total". It is also dropped when the cache-write total is absent (`sdks/typescript/src/providers/anthropic.ts:48`).
- The span name is given as `` `${operation} ${model}` ``. When the request has no string `model`, the name is only the operation (`common.ts:89-92`).
- Coverage: the Interface does not mention `messages.parse()` for Anthropic. In `@anthropic-ai/sdk` it goes through `create`, so it is traced (`node_modules/@anthropic-ai/sdk/resources/messages/messages.js:76`). The doc names only `create` and `messages.stream()`.

## docs-site/EGM.md

- Verification says the command is "the `docs-site` job in `.github/workflows/ci.yml`", but that job has two more steps after `npm run build`: `uv run python scripts/gen_config_table.py --check` and `uv run python scripts/gen_otlp_matrix.py --check` from `backend/` (`.github/workflows/ci.yml:172`, `:174`; line numbers as of this check, the file was being edited concurrently). The documented command passes locally while CI can still fail on these two checks.
- Coverage: two committed pages under `src/content/docs/` are generated by backend scripts rather than written by hand: `configuration.md` (`backend/scripts/gen_config_table.py:8`, `:31`) and `reference/otlp-compatibility.mdx` (`backend/scripts/gen_otlp_matrix.py:28`). A hand edit to either fails the CI checks above. The contract never says this.
- Side Effects lists only `DOCS_SITE_URL` as an environment input. The build also reads `LEGAL_PAGES`, which adds the Privacy/Terms/Imprint/Accessibility footer links only when it is `1` (`docs-site/src/components/Footer.astro:19`, `:28-35`), and `DOCS_SYNC_SKIP_TARGET_CHECK`, which turns off the link-target existence check (`docs-site/scripts/sync-docs.mjs:135`). The Docker build sets both (`deploy/Dockerfile:33-37`).
- Invariant (line 29) says `sync-docs.mjs` "exits non-zero on a broken repository link, unless `--lenient` is passed". When `DOCS_SYNC_SKIP_TARGET_CHECK=1` (the Docker build), a link to a missing file is not detected at all (`docs-site/scripts/lib/links.mjs:105-111`), so the image build never fails on one. Only CI catches it.
- Side Effects says `DOCS_SITE_URL` "sets `site`". It also controls whether `llms.txt` is built (`astro.config.mjs:15`), and through `Astro.site` whether the "Report a problem" issue body carries an absolute URL (`src/components/ReportProblem.astro:10`, `src/lib/report-problem.mjs:14-16`). The line-28 invariant covers llms.txt, but the effect on the issue links is not stated.
- Coverage: the build reads `src/content/docs/reference/detectors.md` from `process.cwd()` to emit catalogue ids (`src/components/CatalogueRedirect.astro:11-12`). A build started from another working directory silently emits an empty list, and the old Doctor anchors stop redirecting.
- Coverage: the Node version in the contract is the `engines` floor (`>=22.12.0`). CI and the Docker build both use Node 24 (`.github/workflows/ci.yml` docs-site `node-version: 24`, `deploy/Dockerfile:30`), and the contract does not say so.

## docs-site/scripts/EGM.md

- The Interface table for `collectPages` has no rows for the two non-Markdown sources that `main` also generates: `backend/app/pricing/prices.py` -> `reference/prices.md` and `backend/openapi.json` -> `api/*.md` (`docs-site/scripts/sync-docs.mjs:162-172`). The Overview mentions them, but the source-to-page map does not.
- Invariant "Every synced page gets frontmatter, an `editUrl` and a closing note that names its GitHub source" holds only for `collectPages` pages (`sync-docs.mjs:138-147`). The OpenAPI pages get `editUrl: false` and no source note (`lib/openapi.mjs:174-180`, `:241-246`). The prices page has no source note. The fallback `security/index.md` has neither (`sync-docs.mjs:149-160`).
- Coverage: when `docs/security/README.md` is missing, `main` writes a generated `security/index.md` overview with its own text (`sync-docs.mjs:149-160`). The contract does not mention it.
- Invariant on `csp.mjs` mentions only `script-src`. The generated policy also fixes `style-src 'self' 'unsafe-inline'`, `img-src 'self' data:`, `font-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, `base-uri 'self'` and `form-action 'self'` (`docs-site/scripts/csp.mjs:78-88`). An operator comparing headers needs the full policy.
- Invariant "Built HTML loads a script, link, image, iframe or media element from another origin" is imprecise. `<link>` is checked only when its `rel` is stylesheet, preload, modulepreload, icon or prefetch. Canonical/alternate links to other origins are allowed. `embed` and `object` are also checked (`csp.mjs:52-58`).

## docs-site/scripts/lib/EGM.md

- Interface says `reference-coverage.mjs` helpers "never throw". `cliSourceFiles` and `ingestSourceFiles` call `fs.readdirSync` without a guard, so they throw on a missing directory (`docs-site/scripts/lib/reference-coverage.mjs:24-29`, `:383-389`).
- Invariant on `links.mjs:resolveTarget` says a private or escaping target "is replaced by its link text". For an image (`![...]`) the whole element is dropped, not replaced by text (`docs-site/scripts/lib/links.mjs:73`). A `?query` suffix on a repository link is also silently dropped, and only a `#fragment` is kept (`links.mjs:89-92`).
- Coverage: `prices.mjs:parseCall` also throws when `provider`, model pattern, `input` or `output` is not a string, and when `min_prompt_tokens` is not a non-negative integer (`docs-site/scripts/lib/prices.mjs:87-92`). The invariant lists only the positional-count and keyword rules.
- Coverage: `topics.test.mjs` in this folder tests `docs-site/src/topics.mjs` (each hand-written page in exactly one topic), not a module of this folder (`docs-site/scripts/lib/topics.test.mjs:7`, `:29`). The contract does not list it.

## docs-site/src/EGM.md

- Side Effects says "None". `Footer.astro` reads `process.env.LEGAL_PAGES` at build time to decide whether to render the legal links (`docs-site/src/components/Footer.astro:19`). `CatalogueRedirect.astro` reads `src/content/docs/reference/detectors.md` from disk at build time (`docs-site/src/components/CatalogueRedirect.astro:11-12`). `ReportProblem.astro` depends on `Astro.site`, which comes from `DOCS_SITE_URL` (`docs-site/src/components/ReportProblem.astro:10`, `docs-site/astro.config.mjs:20`).
- Interface says `TOPICS` is "Imported by `docs-site/astro.config.mjs`" and gives no consumer for `topicSlugs`. Both are also imported by `docs-site/scripts/lib/topics.test.mjs:7`, which enforces that every hand-written page appears in exactly one topic. That is an invariant the contract omits.
- Coverage: `content/docs/` mixes hand-written pages with pages that must not be edited by hand. Some are git-ignored outputs of `sync-docs.mjs` (`api/`, `runbooks/`, `security/`, `deploy/`, `changelog.md`, `reference/prices.md`; `.gitignore:62-68`). Others are committed outputs of backend generators (`configuration.md`, `reference/otlp-compatibility.mdx`; `backend/scripts/gen_config_table.py:8`, `backend/scripts/gen_otlp_matrix.py:28`). The contract does not warn contributors.
- Invariant says "None enforced in code here" and points only to `checkPage`. The `PHASE_2_PAGES` `Sources:` rule and the topic-membership rule are also enforced by tests that read this folder (`docs-site/scripts/lib/page-contract.test.mjs`, `docs-site/scripts/lib/topics.test.mjs`).

## docs-site/src/lib/EGM.md

- Coverage (site URL source): `pageAddress(pathname, site)` receives `Astro.site` from `ReportProblem.astro:10`. `Astro.site` is set only from the `DOCS_SITE_URL` environment variable (`docs-site/astro.config.mjs:20`). The contract says "a configured site" but never names the variable, so an operator cannot tell how to get absolute page URLs in issue reports.
- Interface shows `reportProblemUrl` returning `<repoUrl>/issues/new?title=Docs: <title>&body=Page: <pageUrl>...` as if unencoded. The query is built with `URLSearchParams`, so title and body are form-encoded, and the body ends with a fixed "What is wrong or missing?" prompt (`docs-site/src/lib/report-problem.mjs:3-7`).
- Coverage: `catalogueIdsFrom` strips a leading YAML frontmatter block before it reads headings (`docs-site/src/lib/anchor-redirect.mjs:56`), and it is fed by a build-time file read in `CatalogueRedirect.astro:11-12`. The page also skips the redirect when the anchor exists on the current page (`CatalogueRedirect.astro:23-30`). None of this is stated.

## deploy/EGM.md

- Side Effects says `compose.yaml` "builds the images `spanlight:local` and `spanlight-web:local`". The images are `${SPANLIGHT_IMAGE:-spanlight:local}` and `${SPANLIGHT_WEB_IMAGE:-spanlight-web:local}` (`deploy/compose.yaml:15`, `:263`). `deploy/.env.example:3-4` sets them to the `ghcr.io/dheerajkarwasra/spanlight*:0.7.0` release images, so a plain `up -d` with the example env pulls the release, and `--build` tags the local build with the release name.
- Interface says `compose.minio.yaml` supplies "`S3_*` and `BACKUP_S3_*` to the api and the worker". `BACKUP_S3_*` goes to the worker only. The api gets only `S3_*` (`deploy/compose.minio.yaml:93-109`). This is deliberate: backup credentials stay out of the network-facing api.
- Interface says `smoke.sh:cleanup` "exits with 0 on a pass and 1 on a failure". It exits with the original status (`deploy/smoke.sh:230-252`). `fail()` gives 1 (`smoke.sh:131-134`), but under `set -euo pipefail` any failing command (curl, jq, docker compose) propagates its own non-zero code. A teardown failure after a pass becomes 1.
- Interface says smoke reads `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `SECRET_KEY`, `WEB_PORT` and `APP_BASE_URL` as "environment variables". It reads them from the env file named by `SMOKE_ENV_FILE`, and a shell export overrides the file (`deploy/smoke.sh:150-159`, `:266-272`). It also requires `APP_BASE_URL` to be an `http://` URL (`smoke.sh:276-279`).
- Side Effects for the `migrate` service lists DDL and `spanlight_app` provisioning only. It also runs `spanlight activate-aggregate-coverage --writers-drained --fresh-install --skip-if-projects-exist` on every `up` (`deploy/compose.yaml:169-171`). Smoke asserts this in `check_new_project_has_coverage` (`deploy/smoke.sh:457-464`). Neither appears in the contract's smoke check list.
- The smoke check list omits two checks the script makes: `/.well-known/security.txt` must answer with a `Contact:` field (`deploy/smoke.sh:334-338`), and the SDK span must show up as `has_traces` (`smoke.sh:497-505`).
- `Dockerfile` hardening/failure list: the build also fails when a Python distribution in the runtime venv declares no licence (`deploy/Dockerfile:72-73`, `scripts/licenses/python_notices.py:101-103`). Only the npm and Go licence failures are listed.
- Coverage: the `Dockerfile` build args `LEGAL_PAGES` (frontend and docs legal pages, `deploy/Dockerfile:16-17`, `:36-37`) and `DOCS_SITE_URL` (docs canonical URLs, sitemap, llms.txt, `:33-34`) are not mentioned. Operators decide both at build time.
- Coverage: the `standalone-gateway` profile only works when `GATEWAY_MODE=standalone` and `GATEWAY_UPSTREAM=gateway:8000` are also set (`deploy/compose.yaml:205-208`, `:267`). The contract lists the profile but not these requirements.
- Coverage: the Postgres service is tuned for a 2 vCPU / 4 GB node (`shared_buffers=1GB`, `max_connections=200`), and on a smaller host it fails to start (`deploy/compose.yaml:110-144`). An operator needs this.
- Coverage: `Caddyfile` listens on `{$PORT:8080}` (`deploy/Caddyfile:13`), so `PORT` must stay above 1023 for the unprivileged web user (`deploy/Dockerfile:159-163`). It also turns compression off for `/gw/*` and the live stream (`Caddyfile:17-18`), runs with `admin off` and `auto_https off` (`:2-3`), and serves `/assets/*` and `/docs/_astro/*` as immutable, with a 404 rather than `index.html` for a missing asset (`:98-112`). None of these appear.
- Coverage (Prometheus rules): `SpanlightNotReady` depends on a blackbox job `spanlight-ready` (`deploy/spanlight-rules.yml:91-92`), which `deploy/prometheus.yml:47-71` does not define. `spanlight:api_expected_replicas` and `spanlight:worker_expected_replicas` are hardcoded `vector(1)` (`spanlight-rules.yml:51-54`). `SpanlightBackupMissing` fires whenever backups are off or skipped (`:198-206`). The worker group fires on no data through `or vector(0)` (`:157-158`). The file header says to adjust all of these before use (`:5-11`), and the contract lists the alerts without that warning.
- Coverage (Prometheus scrape): targets are found through Docker DNS `A` records of `api` and `worker` (`deploy/prometheus.yml:54-57`, `:68-71`), so Prometheus must join the compose network (`spanlight_default`, `:19-26`). `/metrics` answers 404 until `METRICS_TOKEN` is set (`:3-5`), and compose defaults it to empty (`deploy/compose.yaml:27`), so on a default stack the scrape and the `SpanlightMetricsScrapeDown` alert fail.
- Coverage: `deploy/.env.example`, the file every operator copies first (`deploy/compose.yaml:2-3`), is not listed in the Interface.

## deploy/railway/EGM.md

- Interface says `preserve()` "also preserves the gateway and rate-limit tuning values". Of the gateway values only `GATEWAY_MAX_IN_FLIGHT` is preserved (`deploy/railway/railway.ts:99`). `GATEWAY_ORG_RPM_CEILING` is hardcoded to `"60"` in the file (`railway.ts:97`), so a value set in Railway is overwritten on the next apply. `preserve` is also an import from `railway/iac` (`railway.ts:31`), not a symbol defined in `railway.ts`.
- Interface lists `PORT=8000` under the api's "Build" settings. It is a runtime environment variable. Only `SPANLIGHT_TARGET` is used as a build argument (`railway.ts:74-75`).
- Coverage: `web` sets `TRUSTED_PROXIES: "0.0.0.0/0 ::/0"` (`railway.ts:232`), so Caddy trusts `X-Real-IP` from any peer, which is safe only because Railway's edge is the sole ingress. This is a security-relevant setting the contract does not mention. It also differs from the `127.0.0.1/32` default the deploy contract describes.
- Coverage: `web.build.watchPatterns` omits `/backend/app/pricing/prices.py` and `/scripts/licenses/**` (`railway.ts:201-210`). The web image's docs build copies `prices.py` to render the Prices page (`deploy/Dockerfile:51`), and the notices script runs in the image build (`deploy/Dockerfile:57-59`). A price-table change therefore does not redeploy the hosted docs. The api and worker patterns also omit `/scripts/licenses/python_notices.py` (`railway.ts:47`, `:131`; `deploy/Dockerfile:72`).
- Coverage: other declared values an operator must keep in sync are not mentioned. `TERMS_VERSION: "2026-11-01"` must equal `frontend/src/features/legal/content/terms.tsx` `VERSION` (`railway.ts:76-77`, `:149-150`). The api also sets `DEMO_ENABLED: "true"` and `APP_BASE_URL` (`railway.ts:79`, `:85`), and api and worker set `SHUTDOWN_*` values (`railway.ts:70-73`, `:144-147`). Other deploy settings are omitted too: the api's `healthcheckTimeout: 120` and `preDeployTimeoutSeconds: 300` (`:61-63`), the worker's `drainingSeconds: 75` (`:141`), and the web's `drainingSeconds: 10` (`:220`).
- Coverage: optional features (S3 exports on api and worker together, backups, OTEL, worker metrics, `WORKER_REQUIRED`, `AGGREGATE_REPAIR_ENABLED`) are commented-out `preserve()` blocks. Each must have a value set in Railway before it is uncommented, or the apply stops (`railway.ts:26-29`, `:105-123`, `:159-192`). The contract states the "stops if missing" rule but not this enable procedure.
- Not verifiable from code: the invariant "Railway deletes any variable that `railway.ts` does not declare" and the claim that `railway config plan` "applies nothing" rest only on the file's comments (`railway.ts:23-24`, `:15-17`). The live Railway configuration was not checked.

## scripts/EGM.md

- Invariant for `check-release-workflow.py` gives the server pipeline as "smoke, trivy, push, sign, latest, SBOM, release". The enforced order has a separate SBOM-signature step (`cosign sign-blob`) between SBOM and release (`scripts/check-release-workflow.py:96-106`).
- The `check-release-workflow.py` rules omit several that the script enforces:
  - No step may carry an `if:`, except the server's `latest` step (`check-release-workflow.py:333-336`).
  - No CI job other than `required` may be conditional (`:316-318`).
  - The `required` gate must be `if: always()` and output the verified SHA (`:303-308`).
  - Trivy must gate on `--severity CRITICAL,HIGH` and must not fail open (`:221-229`).
  - The `latest` tag must be skipped for pre-releases (`:231-234`).
  - `id-token: write` and `packages: write` are allowed only on the named jobs (`:49-54`, `:152-170`).
  - `verify` must call `./.github/workflows/ci.yml` with no inputs or secrets (`:280-288`).
- `check-release-workflow.py` reads `ci.yml` without the existence check it applies to `release.yml`, so a missing `ci.yml` raises an unhandled exception instead of printing `FAIL:` (`check-release-workflow.py:392-393`). The contract says it prints `FAIL:` lines and exits with 1.
- `check-otlp-matrix-workflow.py`: the contract says the `report` job "opens issues". The script requires it to find or create a single issue titled `OTLP compatibility matrix: breakage` with the label `otlp-matrix`, and to `need` every other job (`scripts/check-otlp-matrix-workflow.py:30-31`, `:71-76`). The contract also says "workflow_dispatch with a boolean `force_failure` input", but the script additionally requires its default to be `false` (`:44-47`).
- Verification gives `scripts/check-links.sh <files>`, but CI runs it on a fixed list: the root `*.md`, the component READMEs, `docs/api-deviations.md`, `docs/decisions/*.md`, `docs/deploy/railway.md`, `docs/performance.md` and `backend/load/README.md` (`.github/workflows/ci.yml:195`). A contributor cannot reproduce CI from the contract.
- `check-links.sh`: an unreadable file makes it exit with 2 even when broken links were also found, and the `FAIL:` line is then not printed (`scripts/check-links.sh:263-267`). `-h`/`--help` exits with 0 (`:36-39`). Neither is stated.

## scripts/docs-verify/EGM.md

- Coverage (prerequisites): `bootstrap.py` works only against an API running with `GATEWAY_ALLOW_INSECURE_BASE_URLS=true` and `CREDENTIALS_KEYS` set (`scripts/docs-verify/bootstrap.py:25-26`, `scripts/docs-verify/README.md` "Run it"). Without them, creating the credential pointing at `http://127.0.0.1:9900` fails. The contract omits this.
- Coverage: every API call sends a fixed `Origin: http://localhost:5173` header (`bootstrap.py:46`, `:133`). The target API's origin allowlist must accept the Vite dev origin, so a stack whose `APP_BASE_URL` is `http://localhost:8080` (compose) refuses all writes. The account email is the fixed `docs-verify@example.com` (`bootstrap.py:49`).
- Invariant on `check_trace.py:wait_for_trace` says it "returns the latest one when the time limit runs out". It returns the latest trace only after `--wait` plus `SETTLE_GRACE` (10 s), so the real upper bound is `wait + 10` seconds (`scripts/docs-verify/check_trace.py:38`, `:147-148`).
- Coverage: `--trace-id` ignores `--since` (`check_trace.py:18`, `:107-108`). `check_trace.py` takes `SPANLIGHT_HOST`, `SPANLIGHT_PAT` and `SPANLIGHT_PROJECT_ID` from `.local/docs-verify.env` only, not from the process environment, and a missing file or key is an uncaught `KeyError`, not exit 2 (`check_trace.py:164-175`).
- `summarize` also emits each span's `name`, `kind`, `finish_reason`, `has_input` and `has_output`, and the trace-level `name`, `environment`, `release`, `user_id`, `session_id` and `total_cost` (`check_trace.py:52-80`). The contract lists only a subset.

## scripts/licenses/EGM.md

- Verification for Python runs `uv run --no-sync python ../scripts/licenses/python_notices.py --check` in whatever environment is present. CI first runs `uv sync --frozen --no-dev --no-install-project`, so the check covers only the runtime set that ships in the image, and only then syncs dev for the pytest step (`.github/workflows/ci.yml:36-40`). Run in a dev environment, the documented command checks the dev dependencies too and can fail, or pass, on a different set than CI and the image.
- Interface names `npm_notices.mjs:parseArgs` as if it were a function of the script. It is `parseArgs` imported from `node:util`, called at module top level (`scripts/licenses/npm_notices.mjs:13`, `:47-57`). The script exports nothing, and `licenceId`, `licenceTexts` and `collect` are module-private.
- Coverage: with `--output`, `npm_notices.mjs` writes a header naming notices that are not npm packages (Caddy, Go modules, Alpine apk licences; `npm_notices.mjs:104-116`), and `python_notices.py` writes one naming the interpreter, Debian packages and bundled native libraries (`scripts/licenses/python_notices.py:21-35`). `npm_notices.mjs` also runs `npm ls` with a 256 MiB output buffer (`npm_notices.mjs:61-65`). None is stated.

## examples/EGM.md

- Coverage: a fault profile can only be attached to a gateway key whose environment is not `production` (`backend/app/gateway/faults.py:8`, `:105`; `examples/README.md` "Both talk to the Spanlight LLM gateway"). The contract says the key has a fault profile but omits this requirement.
- Coverage: when `corrected_client.py`'s `_ends_run` returns true (400, 401, 403 or `IncompleteStreamError`), the exception propagates out of `run_rounds` and `main` (`examples/lab_common.py:216-217`, `examples/corrected_client.py:64-68`). The run then ends with a Python traceback and no summary line, which contradicts Side Effects "`main` prints one outcome line per round and a summary line". `main` otherwise always returns 0, even when every round failed (`lab_common.py:252-265`).
- Interface describes `make_client` and `run_rounds` as "classes". They are module functions (`examples/lab_common.py:158`, `:180`). `main` takes `(variant, run, description, argv=None)` (`lab_common.py:232-237`), and `--scenario` defaults to `healthy` (`:240`). Neither is stated.
- Coverage: the request timeouts are part of each policy: the naive client uses 2 s (`examples/naive_client.py:35`), the corrected client 60 s (`examples/corrected_client.py:32`). The corrected client does not retry connection errors or timeouts at all, only `APIStatusError` 429/5xx (`corrected_client.py:73-83`). An unparseable `Retry-After` (including an HTTP-date) is treated as 1 s (`corrected_client.py:35`, `:104-109`). The naive client also strips a rejected parameter from the request before resending (`naive_client.py:91-93`). The `unsupported_parameter` scenario sends `top_k: 40` (`lab_common.py:39`, `:101-103`).
- Coverage: `DEFAULT_BASE_URL` (`http://localhost:8000/gw/v1`) points at the api dev server directly. On the compose stack the gateway is reached through the web port, by default `http://localhost:8080/gw/v1` (`deploy/compose.yaml:270`, `deploy/Caddyfile:52-67`), so `--base-url` is needed there.
- `ruff.toml` also ignores `ANN401` (`examples/ruff.toml`). The contract lists only the selected rule sets.


## Resolution, 2026-11-09

Every finding above was checked against the code again before the contract was changed: 494 corrected in the contracts, 3 rejected because the code shows the finding was wrong:

- `frontend/src/features/gateway/routes/EGM.md`: discard resets to the route prop, which is routeQuery.data (route-editor-page.tsx:32,65); reloadLatest refetches that same query (use-route-save.ts:50), so the prop is never older than the reloaded version
- `frontend/src/features/doctor/EGM.md`: features/alerts defines its own PagerDuty SEVERITY_LABELS (frontend/src/features/alerts/channel-schema.ts:33) and severity-select.tsx:10 imports it from ./channel-schema; nothing in alerts imports features/doctor
- `frontend/src/features/scores/EGM.md`: features/annotations defines its own ScoresPanel (frontend/src/features/annotations/scores-panel.tsx:45) and annotate-page.tsx:15 imports it from ./scores-panel, not from features/scores

Where the code looks wrong rather than the contract, the contract now describes current behaviour and carries a "Known issue:" line. They are in:

- `backend/accuracy/EGM.md`
- `backend/app/backups/EGM.md`
- `backend/app/email/providers/EGM.md`
- `backend/app/insights/detectors/EGM.md`
- `backend/app/jobs/tasks/EGM.md`
- `deploy/railway/EGM.md`
- `frontend/src/components/ui/EGM.md`
- `frontend/src/features/experiments/EGM.md`
- `frontend/src/features/legal/EGM.md`
- `frontend/src/features/legal/content/EGM.md`
- `frontend/src/features/overview/EGM.md`
- `frontend/src/lib/EGM.md`
- `scripts/EGM.md`
- `sdks/typescript/src/EGM.md`
- `sdks/typescript/src/providers/EGM.md`

`frontend/public/EGM.md` was removed, because Vite copies that folder to the site root and would serve it publicly. Its content is in `frontend/EGM.md`.
