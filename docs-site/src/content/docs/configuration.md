---
title: Configuration
description: Every environment variable the Spanlight API and worker read, and the variables of the Compose file.
sidebar:
  order: 21
---

Spanlight is configured through environment variables. The `api` and `worker` roles read the same ones; the Compose file in `deploy/compose.yaml` passes them to both. A variable that is set to an empty string counts as unset, so `KEY=` in an env file means "not configured".

A deployment needs three secrets to start: `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `SECRET_KEY` (see [Self-hosting](/docs/self-hosting/)). Everything else is optional, and each optional integration (email, object storage, sign in with GitHub or Google, self-tracing, application-level encryption) is switched on by its own variable. A request that needs an integration that is not configured answers `409 NOT_CONFIGURED` and names the setting; a background job that needs one ends as `skipped_not_configured`. Nothing pretends to succeed.

## Application settings

These tables are generated from the application's settings class, so they list exactly what the running code reads. A secret's default is never printed.

<!-- BEGIN GENERATED SETTINGS: written by backend/scripts/gen_config_table.py -->

### Core

The settings every deployment needs to look at.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | string | `postgresql+psycopg://spanlight_app:***@localhost:5432/spanlight` (local development) | SQLAlchemy URL the application connects to. The role must not be a superuser and must not have `BYPASSRLS`, or row-level security is silently skipped. The Compose file builds it from `APP_DB_PASSWORD`. |
| `APP_BASE_URL` | string | `http://localhost:8000` | The public URL the dashboard is served from. It is the allowed `Origin` for state-changing requests and the base of the links in emails and OAuth callbacks. An `https` URL turns on `Secure` cookies with `__Host-` names. An `https` URL, or any host other than `localhost`, `127.0.0.1` or `[::1]`, requires a real `SECRET_KEY`. |
| `ALLOWED_ORIGINS` | comma-separated list | empty | Extra origins allowed to make state-changing requests, comma-separated. Leave empty unless another site must call the API from a browser. |
| `SECRET_KEY` | secret | built-in development value (do not use) | HMAC key for CSRF tokens and for the short-lived signed state of two-factor and GitHub or Google sign-in. Required when `APP_BASE_URL` uses `https` or is not local: the application refuses to start with the built-in development value. It must be at least 32 characters unless `APP_BASE_URL` is `localhost`, `127.0.0.1` or `[::1]`. Generate a long random string. Rotating it does not sign anyone out. |
| `CREDENTIALS_KEYS` | secret | unset | Master keys that encrypt stored secrets such as two-factor seeds and gateway provider credentials, as `<key_id>:<base64 of 32 bytes>`, comma-separated. The first entry encrypts new data; keep older entries so existing data stays readable. Unset leaves two-factor authentication and the gateway's provider credentials unavailable. The worker needs the same value as the API. Without it the worker cannot open any stored secret: experiment runs and LLM judge evaluators fail with `credentials_not_configured`, webhook evaluators fail with `webhook_secret_unreadable`, Slack, webhook and PagerDuty alerts are not delivered, and demo traffic is skipped. The worker logs a `worker_credentials_keys_missing` warning at start-up. Back the value up: a lost key cannot be recovered. |
| `TERMS_VERSION` | string | unset | The version of the hosted terms of service that users must accept, for example `2026-11-01`. Set, signup asks for acceptance and signed-in users are asked to accept when the version changes. Leave it unset on a self-hosted instance: no terms are asked for anywhere. |

### Email

Email is optional. It is on when `EMAIL_PROVIDER` is `resend` or `smtp` (with what it needs), or `console` with `EMAIL_CONSOLE_FILE` set. Features that depend on email, such as address verification and password reset, stay off without it.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `EMAIL_PROVIDER` | `console` \| `resend` \| `smtp` | `console` | How email is sent. `resend` needs `RESEND_API_KEY` and `EMAIL_FROM`; `smtp` needs `SMTP_HOST` and `EMAIL_FROM`. `console` only logs the recipient and subject. |
| `EMAIL_FROM` | string | unset | Sender address, for example `Spanlight <noreply@example.com>`. Required for `resend` and `smtp`. |
| `RESEND_API_KEY` | secret | unset | API key of the Resend account. Required for `resend`. |
| `SMTP_HOST` | string | unset | SMTP server host name. Required for `smtp`. |
| `SMTP_PORT` | integer | `587` | SMTP server port. `465` uses implicit TLS (SMTPS); any other port uses STARTTLS. |
| `SMTP_USERNAME` | string | unset | SMTP user name. Optional. |
| `SMTP_PASSWORD` | secret | unset | SMTP password. Optional. |
| `SMTP_STARTTLS` | boolean | `true` | Upgrade the SMTP connection with STARTTLS. Turn it off only for a trusted local relay: mail and password then travel in clear text. |
| `EMAIL_CONSOLE_FILE` | path | unset | With the `console` provider, also append each message to this file as one JSON line. For development and tests: it counts as email being configured. |

### Sign in with GitHub and Google

A provider is on exactly when both of its values are set. Setting only one of a pair stops the application at startup.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `OAUTH_GITHUB_CLIENT_ID` | string | unset | Client ID of a GitHub OAuth app. Callback URL: `<APP_BASE_URL>/api/v1/auth/oauth/github/callback`. |
| `OAUTH_GITHUB_CLIENT_SECRET` | secret | unset | Client secret of the GitHub OAuth app. |
| `OAUTH_GOOGLE_CLIENT_ID` | string | unset | Client ID of a Google OAuth client. Callback URL: `<APP_BASE_URL>/api/v1/auth/oauth/google/callback`. |
| `OAUTH_GOOGLE_CLIENT_SECRET` | secret | unset | Client secret of the Google OAuth client. |

### Object storage

Trace exports need an S3-compatible store. It is on when the bucket and both keys are set; setting only some of them stops the application at startup. A request that needs storage while it is off answers `409 NOT_CONFIGURED`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `S3_BUCKET` | string | unset | Bucket for trace exports. Keep it private and encrypted at rest. |
| `S3_ACCESS_KEY` | secret | unset | Access key ID for the bucket. |
| `S3_SECRET_KEY` | secret | unset | Secret access key for the bucket. |
| `S3_REGION` | string | unset | Region of the bucket. AWS buckets need it, for example `eu-west-1`. |
| `S3_ENDPOINT` | string | unset | Endpoint URL of a non-AWS service such as MinIO or Cloudflare R2. |
| `S3_PUBLIC_ENDPOINT` | string | unset | The address browsers reach the store at, when that differs from `S3_ENDPOINT` (for example `http://minio:9000` inside Docker and `http://localhost:9000` outside). Download links are signed against it. |
| `S3_FORCE_PATH_STYLE` | boolean | `false` | Address buckets as `<endpoint>/<bucket>` instead of `<bucket>.<endpoint>`. MinIO and most self-hosted services need this. |

### Backups

The nightly backup job runs when `BACKUPS_ENABLED` is true, explicit backup storage is configured and `BACKUP_DATABASE_URL` is set. Otherwise it ends as `skipped_not_configured`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `BACKUPS_ENABLED` | boolean | `true` | Set to `false` to turn the nightly backup off. |
| `BACKUP_DATABASE_URL` | secret | unset | Connection URL `pg_dump` uses. It must use a superuser or a role with `BYPASSRLS`, never the application role: the tenant tables force row-level security and a dump fails without bypassing it. |
| `BACKUP_S3_BUCKET` | string | unset | Independent private backup bucket; never falls back to S3_BUCKET. |
| `BACKUP_S3_ACCESS_KEY` | secret | unset | Backup principal access key; routine workers need Put/Abort only. |
| `BACKUP_S3_SECRET_KEY` | secret | unset | Backup principal secret; supply independent recovery or retention keys only to operators. |
| `BACKUP_S3_ENDPOINT` | string | unset | Independent S3-compatible backup endpoint; unset uses AWS. |
| `BACKUP_S3_REGION` | string | unset | Independent backup signing region; defaults to us-east-1. |
| `BACKUP_S3_FORCE_PATH_STYLE` | boolean | `false` | Use path-style addressing for backup storage (MinIO). |
| `BACKUP_S3_SSE` | string | `AES256` | Server-side encryption requested on backup uploads; blank sends none (MinIO without KMS). |

### Worker and health

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `WORKER_REQUIRED` | boolean | `true` | `/health/ready` answers `503` when no worker has reported in the last 120 seconds. Set to `false` only for a deployment that deliberately runs the API without a worker; notifications and exports only move while a worker runs. |
| `WORKER_METRICS_PORT` | integer (min 1, max 65535) | unset | Port on which the worker serves `/metrics`, with the same `METRICS_TOKEN` rule as the API. Unset leaves the listener off, and then the worker's job, outbox, notification and rollup metrics are not exposed. |
| `SHUTDOWN_TIMEOUT_SECONDS` | number (greater than 0) | `60` | Total first-signal shutdown envelope; later signals never reset it. External stop grace must exceed it (75s requested by default). Unjoined database users retain their pools past expiry until external hard stop; accounting remains incomplete. |
| `SHUTDOWN_WORK_SECONDS` | number (greater than 0) | `50` | Work allowance before cancellation. With defaults, producer joins end at 52.5s, writes at 53.75s and cancellation joins at 55s. Must leave room for joins and both reserves. |
| `SHUTDOWN_FLUSH_SECONDS` | number (greater than 0) | `3` | Final loss-flush reserve before resource close (default 55-58s). Failed or uncertain writes remain incomplete. |
| `SHUTDOWN_CLOSE_SECONDS` | number (greater than 0) | `2` | Resource-close reserve at the end of the original deadline (default 58-60s); tracing uses only remaining time. |

### Demo workspace

The live demo is fed by real model calls under a monthly budget. It is on by default exactly when `ANTHROPIC_API_KEY` is set.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | secret | unset | Anthropic API key for the demo workspace's live model calls. |
| `DEMO_ENABLED` | boolean | unset | Force the demo workspace on or off. Unset means on exactly when `ANTHROPIC_API_KEY` is set. |
| `DEMO_MONTHLY_BUDGET_USD` | decimal | `1.00` | Month-to-date spending limit for the demo's model calls, in US dollars. The demo job checks it before every run (two provider calls). |

### LLM gateway

The gateway serves `/gw/v1/*` for OpenAI- and Anthropic-compatible clients. Provider credentials are sealed with `CREDENTIALS_KEYS`; without it, saving a credential answers `409 NOT_CONFIGURED`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `GATEWAY_MODE` | `embedded` \| `standalone` \| `disabled` | `embedded` | Where the gateway runs. `embedded` serves it from the API process, `standalone` leaves it to a separate `python -m app.gateway` process (point `GATEWAY_UPSTREAM` at it) and `disabled` turns it off. In the last two the API answers `/gw/*` with `404`. |
| `GATEWAY_MAX_IN_FLIGHT` | integer (min 1) | `512` | Most concurrent HTTP gateway requests per process in embedded or standalone mode. Excess requests get provider-shaped 503 with Retry-After before body/authentication. A slot lasts through stream cleanup. Dashboard/health capacity is independent; internal playground and worker calls are outside this cap. Replicas/processes multiply capacity and memory; `GATEWAY_MAX_IN_FLIGHT_BYTES` is what bounds memory. |
| `GATEWAY_MAX_IN_FLIGHT_BYTES` | integer (min 10485760) | `83886080` | Most declared request-body bytes of in-flight gateway requests per process (80 MiB, at least 10 MiB). A request reserves its `Content-Length`, or the full 10 MiB request cap when the body is chunked or undeclared; `GET`, `HEAD` and `OPTIONS` reserve nothing. One that does not fit gets the same 503. |
| `GATEWAY_MAX_IN_FLIGHT_PER_KEY` | integer (min 1) | `256` | Most in-flight gateway requests per process with the same gateway key, read as authentication reads it from `Authorization` or `x-api-key`, so one caller cannot take every slot. Excess requests get the same 503. |
| `GATEWAY_ALLOW_INSECURE_BASE_URLS` | boolean | `false` | Allow provider credentials to use `http://` base URLs and private, loopback or link-local addresses, for a local model server. Off, a base URL must be `https://` and resolve only to public addresses. Keep it off on a shared deployment. |
| `GATEWAY_ORG_RPM_CEILING` | integer (min 1, max 100000) | unset | Requests per minute one organization may send through the gateway, across all its keys, checked before each key's own limits. Unset means no ceiling. Set it on a shared deployment so one organization cannot crowd out the others. |
| `GATEWAY_RECORD_CONCURRENCY` | integer (min 1, max 10) | `8` | Most gateway spans written to the database at once. Spans are written after the answer has been sent, and each write holds a connection of the pool requests use (10, with 10 more on overflow), so keep it at or below 10: more writers than that let a burst of span writes take the connections gateway calls need. |
| `GATEWAY_RECORD_BACKLOG` | integer (min 1, max 100000) | `5000` | Most gateway spans waiting or being written, a failed write waiting to be retried included. Each holds its input and output already cut to 1 MiB; the memory a waiting span takes in the process is larger than its stored JSON, so size the backlog by what the api can spare. Past it a span is dropped and counted in `spanlight_gateway_record_failures_total` and in `gateway_spans_lost` on `/health/ready`; the call itself is never affected. |
| `GATEWAY_RECORD_BACKLOG_MB` | integer (min 1, max 65536) | `128` | Most megabytes (MiB) of payloads the waiting gateway spans may hold together, counted as their input and output stored JSON. A span that would pass it is dropped and counted like one past `GATEWAY_RECORD_BACKLOG`, unless it is the only one waiting. With ordinary payloads the span count binds first; this bounds a backlog of large ones. |

### Alerts

Alert rules notify channels: email recipients, a Slack incoming webhook, a signed HTTP webhook or a PagerDuty service. Slack, webhook and PagerDuty channels keep their secret sealed with `CREDENTIALS_KEYS`; without it, saving one answers `409 NOT_CONFIGURED`. Email channels send through the email provider above.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ALERTS_EVALUATION_ENABLED` | boolean | `true` | Schedule the alert evaluation job, which checks every enabled rule once a minute. Off, no alert fires. |
| `WEEKLY_DIGEST_ENABLED` | boolean | `true` | Schedule the weekly digest email sent on Monday mornings. |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | boolean | `false` | Allow webhook channels to use `http://` URLs and private, loopback or link-local addresses, for a receiver inside your network. Off, a webhook URL must be `https://` and resolve only to public addresses. Keep it off on a shared deployment. |
| `ALERT_EMAIL_ANY_RECIPIENT` | boolean | `false` | Allow email channels to send to any address. Off, every recipient must be a member of the organization with a verified email. Keep it off on a shared deployment. |
| `PAGERDUTY_EVENTS_URL` | string | `https://events.pagerduty.com/v2/enqueue` | Where PagerDuty channels send events (Events API v2). |

### Doctor

Detectors read each project's recent spans every 15 minutes and turn what they find into insights with a cause and a fix. "Explain with Claude" sends one insight's evidence to Claude through the organization's own Anthropic gateway credential, so it is real spend in that organization, capped by the monthly budget.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `DETECTORS_ENABLED` | boolean | `true` | Schedule the detector job, which looks for problems in every project with traffic every 15 minutes. Off, no new insights appear. |
| `PROVIDER_STATUS_POLLING_ENABLED` | boolean | `true` | Schedule the poll of the Anthropic, OpenAI and Google public status pages every 5 minutes, whose incidents the provider pages and the Doctor cite. The feed URLs are fixed. Off, nothing is fetched (an air-gapped instance) and the incident list says polling is disabled. |
| `EXPLAIN_MODEL` | string | `claude-sonnet-5-5` | Claude model that explains an insight. It must have a price, or explanations answer `409 EXPLAIN_MODEL_UNPRICED`. |
| `EXPLAIN_MONTHLY_BUDGET_USD` | decimal (min 0) | `1.00` | Most an organization may spend on explanations per UTC month, in US dollars. `0` turns explanations off (`409 NOT_CONFIGURED`). |
| `AGGREGATE_REPAIR_ENABLED` | boolean | `false` | Consume durable correction markers every five minutes. Capture and pending metadata remain active when off; affected materialized observations and alerts remain pending. Enable after the aggregate repair cutover procedure and verify relevant backlog convergence. |
| `USER_STATS_ENABLED` | boolean | `true` | Schedule the refresh of per-user daily statistics behind the Users page. Off, the page stops updating. |

### Evaluations

Evaluators score traces: heuristics run locally, LLM judges call a model through one of the organization's provider credentials under each evaluator's monthly budget, and webhooks ask your own service. Online evaluation samples recent spans every minute; experiments run a dataset through a prompt and score every item.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `EVALS_ENABLED` | boolean | `true` | Schedule online evaluation, which samples recent spans for every enabled evaluator once a minute. Off, evaluators run only inside experiments and test runs. |
| `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` | integer (min 1, max 10000) | `50` | Most evaluation runs one evaluator gets per one-minute sampling cycle. Spans past it are not sampled in that cycle. An LLM judge whose monthly budget is used up is not sampled at all. |
| `EXPERIMENT_MAX_ITEMS` | integer (min 1, max 100000) | `1000` | Ceiling for an experiment's `max_items`: the most dataset items one experiment run processes. |
| `EXPERIMENT_SCHEDULES_ENABLED` | boolean | `false` | Schedule the daily job that reruns every experiment marked `weekly` once per ISO week, unattended, each run under its own cost limit. Off, a saved schedule starts nothing. See [weekly reruns](/docs/experiments/#weekly-reruns). |

### Review loop

Every UTC day a project's review policy fills its review queue with the traces worth reading, each with its reason. A replay sends a stored LLM request again through one of the organization's provider credentials, sealed with `CREDENTIALS_KEYS`; without it replay answers `409 NOT_CONFIGURED`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `REVIEW_SELECTION_ENABLED` | boolean | `true` | Schedule the nightly selection of traces into each project's review queue. Off, a review policy can still be run by hand. |
| `REPLAY_MONTHLY_BUDGET_USD` | decimal (min 0) | `5.00` | Most a project may spend on replays per UTC month, in US dollars. `0` turns replay off (`409 NOT_CONFIGURED`). |

### Billing sources

A billing source connects a provider's cost report (an Anthropic or OpenAI admin key, or a CSV upload) so the reconciliation page can compare what the provider billed with what Spanlight priced. Admin keys are sealed with `CREDENTIALS_KEYS`; without it an API-kind source cannot be created or rotated (`409 NOT_CONFIGURED`). CSV sources work without it.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `BILLING_SOURCES_ENABLED` | boolean | `true` | Schedules the daily fetch of connected provider cost reports; `false` stops every provider cost-API call, the routes stay and a manual refresh answers `409 NOT_CONFIGURED`. |

### Audit log

Every organization keeps an append-only log of who changed what, sign-ins and refused changes included. Admins and owners read it under Settings, Audit log.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `AUDIT_RETENTION_DAYS` | integer (min 0) | `365` | Days an audit event is kept; the hourly retention job deletes older ones. `0` keeps every event. |

### Attribution

The attribution view groups spend and usage by trace metadata, trace name, end user or release. Windows of 24 hours or less read the traces live; longer windows read daily rollups that the refresh job rebuilds every 15 minutes.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ATTRIBUTION_ENABLED` | boolean | `true` | Schedule the attribution refresh, which rebuilds the daily rollups behind windows longer than 24 hours every 15 minutes. Off, those windows stop updating; windows of 24 hours or less stay live. |

### API request limits

A request that would otherwise wait on a busy database fails fast with `503` and `Retry-After` instead of queueing behind slow work. The worker, exports and deleting an organization or project are not subject to the statement limit.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `API_POOL_TIMEOUT_SECONDS` | number (min 1, max 60) | `1.0` | Seconds an API request waits for a connection from the main pool. Past it the request answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 5`. Blank keeps the default. |
| `API_STATEMENT_TIMEOUT_SECONDS` | number (min 1, max 300) | `10.0` | Longest one database statement of an API request may run. The database cancels it and the request answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 5`. Blank keeps the default. |
| `API_MAX_IN_FLIGHT` | integer (min 1) | `64` | Most `/api` and `/v1` requests one api process serves at once. Past it a request answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 1` at once instead of queueing. `/gw` and `/health` are not counted. Blank keeps the default. |
| `API_KEY_CACHE_TTL_SECONDS` | number (min 0) | `10` | Seconds one api process reuses a project API key's row, and a gateway key's context (project, route, provider credential, Integration Lab profile and the project's payload capture and contact redaction settings), before reading it again. A key revoked or edited, or a route, credential, profile, capture or redaction setting changed, through another api process keeps its old value on this one for up to this long; a change on the same process takes effect at once. `0` reads them on every request. Blank keeps the default. |

### Ingest caps

What one project may send through `POST /v1/traces` and `POST /v1/otlp/traces`, on top of the per-key request limit. Over a cap the request is `429` with `Retry-After`. Concurrency refusals happen before the body is read, so their span count is unknown and they are not counted as telemetry losses. Span-rate and daily-quota refusals are counted and shown to the project (the completeness card and the notices on Overview and Traces). `0` disables only the span-rate and daily-quota caps. One project's own span-rate and daily-quota caps: `spanlight set-ingest-caps <project-id> --spans-per-day N` (and `--bytes-per-day`, `--spans-per-second`; `default` clears one). The gateway's own spans are governed by the gateway key limits instead. See the [ingestion spike runbook](/docs/runbooks/ingestion-spike/#per-project-caps).

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `INGEST_MAX_IN_FLIGHT_PER_PROJECT` | integer (min 1) | `8` | Concurrent span-ingest requests one project may hold per process, shared across its keys and both ingest routes. Minimum 1; blank keeps the default. Checked after key authentication, scope and the per-key bucket, before reading the body; held until the request finishes. Over it the request is `429 RATE_LIMITED` with `Retry-After: 1`. The default leaves most of a process's database connections (20) to other projects; replicas multiply this limit. Refusals increment `spanlight_rate_limit_rejections_total` with `scope="ingest_project_concurrency"`. |
| `INGEST_SPANS_PER_SECOND` | integer (min 0) | `1000` | Sustained spans a second one project may ingest, with bursts of at least a full 1,000-span batch. Over it the request is `429 RATE_LIMITED` with `Retry-After` in seconds. `0` is no rate cap. |
| `INGEST_SPANS_PER_DAY` | integer (min 0) | `1000000` | Spans one project may store per UTC day (replays of stored spans count nothing). Once used up, a request with new spans is `429 INGEST_QUOTA_EXCEEDED` with `Retry-After` until midnight UTC. `0` is no daily span cap. |
| `INGEST_BYTES_PER_DAY` | integer (min 0) | `5368709120` | Decompressed request body bytes one project may ingest per UTC day; the same `429` as the span quota once used up. `0` is no daily byte cap. |

### Connection pools

Idempotency keys and rate limiting each use a small pool of their own, so they cannot starve the main one. Change these only after reading the [scaling runbook](/docs/runbooks/scale/).

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `IDEMPOTENCY_POOL_SIZE` | integer (min 1) | `5` | Connections in the idempotency-key pool. |
| `IDEMPOTENCY_POOL_TIMEOUT_SECONDS` | number (greater than 0) | `5.0` | Seconds a request waits for an idempotency connection before it fails. |
| `RATE_LIMIT_POOL_SIZE` | integer (min 1) | `5` | Connections in the rate-limit pool. |
| `RATE_LIMIT_POOL_TIMEOUT_SECONDS` | number (greater than 0) | `0.25` | Seconds a rate-limit check waits for a connection. A check that cannot get one is skipped and the request goes through, so keep this short. |
| `RATE_LIMIT_LOCK_TIMEOUT_MS` | integer (greater than 0) | `100` | Milliseconds each token-bucket SQL lock wait may take. Must be positive and less than RATE_LIMIT_STATEMENT_TIMEOUT_MS. SQL errors skip the check and increment unavailable metrics. Separate from connection checkout and request/cancellation cleanup time. |
| `RATE_LIMIT_STATEMENT_TIMEOUT_MS` | integer (greater than 0) | `250` | Milliseconds a token-bucket SQL statement may take. Must exceed RATE_LIMIT_LOCK_TIMEOUT_MS. Applies only to the dedicated limiter pool; authentication throttles and budgets retain their existing failure policies. This is not a full request deadline. |

### Monitoring and logging

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `METRICS_TOKEN` | secret | unset | Bearer token that protects `/metrics`. Unset makes `/metrics` answer `404`. The endpoint is served by the API directly, not through the web port. |
| `API_DOCS` | boolean | unset | Serve the Swagger UI at `/api/docs`, whose script comes from jsDelivr. Unset means on when `APP_BASE_URL` is http and off (`404`) when it is https. `/api/openapi.json` is served either way. |
| `SENTRY_DSN` | string | unset | Sentry DSN for error reporting. Request bodies, cookies and query strings are never sent. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | string | unset | Base URL of an OTLP HTTP collector for Spanlight's own traces (spans go to `<endpoint>/v1/traces`; a value ending in `/traces` is used as it is, and a `/v1/otlp` base gets `/traces`). Unset turns self-tracing off. Request bodies, headers and query parameters are never recorded. |
| `OTEL_SERVICE_NAME` | string | `spanlight-api` | Service name on Spanlight's own spans. |
| `LOG_LEVEL` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_JSON` | boolean | `true` | Write logs as JSON lines. Turn off for human-readable development output. |

### Enterprise edition

Code under `ee/` directories is licensed separately from the MIT core and runs only with a license key. Without one the application runs as the community edition, which is complete on its own. `spanlight license-status` shows what a key grants.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `EE_LICENSE_KEY` | secret | unset | An enterprise license key, `spl_lic_...`. Unset runs the community edition. A key that does not verify stops the application at startup; an expired key turns the features it granted off, and requests for them answer `403 EE_LICENSE_REQUIRED`. Set the same value on the API and the worker. Verified offline: nothing is sent anywhere. |

<!-- END GENERATED SETTINGS -->

## Compose and web variables

These are read by `deploy/compose.yaml` and the `web` role, not by the application.

| Variable | Default | Description |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | none, required | Password of the `postgres` owner role. Used by the migration step only. |
| `APP_DB_PASSWORD` | none, required | Password of the unprivileged `spanlight_app` role the API and worker connect as. The migration step creates the role with it. |
| `WEB_PORT` | `8080` | Host port the `web` service is published on. |
| `TRUSTED_PROXIES` | `127.0.0.1/32` | Address ranges of a reverse proxy in front of `web` that always sets `X-Real-IP`, as space-separated CIDRs. Without it a client cannot choose the address that rate limits and session records see. |
| `PORT` | `8080` | Port Caddy listens on inside the `web` container. Platforms such as Railway set it. |
| `API_UPSTREAM` | `api:8000` | Where `web` proxies `/api`, `/v1` and `/health` to. |
| `GATEWAY_UPSTREAM` | `api:8000` | Where `web` proxies `/gw/*` (the LLM gateway) to. The default is the API, which serves the gateway when `GATEWAY_MODE=embedded`. With `GATEWAY_MODE=standalone` and the `standalone-gateway` Compose profile, set it to `gateway:8000`. |

## MinIO variables

<!-- Sources: deploy/compose.minio.yaml -->

These are read by `deploy/compose.minio.yaml`, the optional file that adds a local MinIO object store for trying exports and backups without a cloud account. Start it with `docker compose -f deploy/compose.yaml -f deploy/compose.minio.yaml --env-file deploy/.env up -d --build`. The file sets the `S3_*` variables of the `api` and `worker` for you: the endpoint `http://minio:9000`, the region `us-east-1`, path-style addressing, and a public endpoint of `http://localhost:<MINIO_API_PORT>` for download links. It provisions distinct export/upload/recovery/retention users. Root credentials stay in MinIO/init, while only worker receives upload-only `BACKUP_S3_*`. Recovery and retention credentials belong to separate operator processes. For production, leave the overlay out and configure export `S3_*` plus independent worker `BACKUP_S3_*` values.

| Variable | Default | Description |
| --- | --- | --- |
| `MINIO_ROOT_USER` | `spanlight-minio` | MinIO administrator user; passed only to MinIO and initialization. The default is public: change it in `deploy/.env` if anyone else can reach the host. |
| `MINIO_ROOT_PASSWORD` | `spanlight-minio-secret` | MinIO administrator password; passed only to MinIO and initialization. The default is public: change it in `deploy/.env` if anyone else can reach the host. |
| `MINIO_API_PORT` | `9000` | Host port of the MinIO API, bound to `127.0.0.1` only. Browsers download exports from it, so it is also the port in `S3_PUBLIC_ENDPOINT`. |
| `MINIO_CONSOLE_PORT` | `9001` | Host port of the MinIO web console, bound to `127.0.0.1` only. |
| `S3_BUCKET` | `spanlight` | Export bucket created by `minio-init`; API and worker use export-only credentials. |
| `BACKUP_S3_BUCKET` | `spanlight-backups` | Separate backup bucket; must differ from export bucket. |
| `MINIO_EXPORT_USER`, `MINIO_EXPORT_PASSWORD` | `spanlight-exports`, `spanlight-exports-secret` | Local export credentials; API and worker receive these as generic S3 keys. |
| `MINIO_UPLOAD_USER`, `MINIO_UPLOAD_PASSWORD` | `spanlight-backup-upload`, `spanlight-backup-upload-secret` | Local Put/Abort principal; only worker receives these as backup keys. |
| `MINIO_RECOVERY_USER`, `MINIO_RECOVERY_PASSWORD` | `spanlight-backup-recovery`, `spanlight-backup-recovery-secret` | Operator List/Get credentials; supplied only to initialization, never app services. |
| `MINIO_RETENTION_USER`, `MINIO_RETENTION_PASSWORD` | `spanlight-backup-retention`, `spanlight-backup-retention-secret` | Operator List/Delete credentials; supplied only to initialization, never app services. |

Backups also need `BACKUP_DATABASE_URL`, which the MinIO file does not set. See [Back up and restore](/docs/self-hosting/backups/).

## Generating secrets

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"      # POSTGRES_PASSWORD, APP_DB_PASSWORD, SECRET_KEY, METRICS_TOKEN
echo "v1:$(openssl rand -base64 32)"                               # CREDENTIALS_KEYS
```

To change a secret on a running deployment, follow the [rotate secrets runbook](/docs/runbooks/rotate-secrets/).

## Related pages

- [Self-hosting](/docs/self-hosting/): What runs, how to expose it and how to keep it healthy.
- [Provider credentials](/docs/gateway/credentials/): Store provider keys encrypted and rotate the master keys.
- [Data, limits and operations](/docs/guides/data/): Rate limits, backups and monitoring Spanlight itself.
- [Rotate secrets](/docs/runbooks/rotate-secrets/): Change a secret on a running deployment.
- [Back up and restore](/docs/self-hosting/backups/): Use the backup settings and rehearse a restore.
- [Troubleshooting](/docs/troubleshooting/): Fix the most common self-hosting problems.
