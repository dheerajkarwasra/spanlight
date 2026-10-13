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
| `APP_BASE_URL` | string | `http://localhost:8000` | The public URL the dashboard is served from. It is the allowed `Origin` for state-changing requests and the base of the links in emails and OAuth callbacks. An `https` URL turns on `Secure` cookies and requires a real `SECRET_KEY`. |
| `ALLOWED_ORIGINS` | comma-separated list | empty | Extra origins allowed to make state-changing requests, comma-separated. Leave empty unless another site must call the API from a browser. |
| `SECRET_KEY` | secret | built-in development value (do not use) | HMAC key for CSRF tokens and for the short-lived signed state of two-factor and GitHub or Google sign-in. Required when `APP_BASE_URL` uses `https`: the application refuses to start with the built-in development value. Generate a long random string. Rotating it does not sign anyone out. |
| `CREDENTIALS_KEYS` | secret | unset | Master keys that encrypt stored secrets such as two-factor seeds and gateway provider credentials, as `<key_id>:<base64 of 32 bytes>`, comma-separated. The first entry encrypts new data; keep older entries so existing data stays readable. Unset leaves two-factor authentication and the gateway's provider credentials unavailable. Back the value up: a lost key cannot be recovered. |

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

Backups and trace exports need an S3-compatible store. It is on when the bucket and both keys are set; setting only some of them stops the application at startup. A request that needs storage while it is off answers `409 NOT_CONFIGURED`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `S3_BUCKET` | string | unset | Bucket for backups and exports. Keep it private and encrypted at rest. |
| `S3_ACCESS_KEY` | secret | unset | Access key ID for the bucket. |
| `S3_SECRET_KEY` | secret | unset | Secret access key for the bucket. |
| `S3_REGION` | string | unset | Region of the bucket. AWS buckets need it, for example `eu-west-1`. |
| `S3_ENDPOINT` | string | unset | Endpoint URL of a non-AWS service such as MinIO or Cloudflare R2. |
| `S3_PUBLIC_ENDPOINT` | string | unset | The address browsers reach the store at, when that differs from `S3_ENDPOINT` (for example `http://minio:9000` inside Docker and `http://localhost:9000` outside). Download links are signed against it. |
| `S3_FORCE_PATH_STYLE` | boolean | `false` | Address buckets as `<endpoint>/<bucket>` instead of `<bucket>.<endpoint>`. MinIO and most self-hosted services need this. |

### Backups

The nightly backup job runs when `BACKUPS_ENABLED` is true, object storage is on and `BACKUP_DATABASE_URL` is set. Otherwise it ends as `skipped_not_configured`.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `BACKUPS_ENABLED` | boolean | `true` | Set to `false` to turn the nightly backup off. |
| `BACKUP_DATABASE_URL` | secret | unset | Connection URL `pg_dump` uses. It must use a superuser or a role with `BYPASSRLS`, never the application role: the tenant tables force row-level security and a dump fails without bypassing it. |

### Worker and health

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `WORKER_REQUIRED` | boolean | `true` | `/health/ready` answers `503` when no worker has reported in the last 120 seconds. Set to `false` only for a deployment that deliberately runs the API without a worker; notifications and exports only move while a worker runs. |
| `WORKER_METRICS_PORT` | integer (min 1, max 65535) | unset | Port on which the worker serves `/metrics`, with the same `METRICS_TOKEN` rule as the API. Unset leaves the listener off, and then the worker's job, outbox, notification and rollup metrics are not exposed. |

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
| `GATEWAY_ALLOW_INSECURE_BASE_URLS` | boolean | `false` | Allow provider credentials to use `http://` base URLs and private, loopback or link-local addresses, for a local model server. Off, a base URL must be `https://` and resolve only to public addresses. Keep it off on a shared deployment. |
| `GATEWAY_ORG_RPM_CEILING` | integer (min 1, max 100000) | unset | Requests per minute one organization may send through the gateway, across all its keys, checked before each key's own limits. Unset means no ceiling. Set it on a shared deployment so one organization cannot crowd out the others. |
| `GATEWAY_RECORD_CONCURRENCY` | integer (min 1, max 256) | `16` | Most gateway spans written to the database at once. Spans are written after the answer has been sent, and each write holds a connection. |
| `GATEWAY_RECORD_BACKLOG` | integer (min 1, max 100000) | `5000` | Most gateway spans waiting or being written, a failed write waiting to be retried included. Each holds its input and output already cut to 32 KB, so a span's payloads are at most 64 KB of JSON as stored; the memory a waiting span takes in the process is larger than that, so size the backlog by what the api can spare. Past it a span is dropped and counted in `spanlight_gateway_record_failures_total` and in `gateway_spans_lost` on `/health/ready`; the call itself is never affected. |

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
| `EXPLAIN_MODEL` | string | `claude-sonnet-5-5` | Claude model that explains an insight. It must have a price, or explanations answer `409 EXPLAIN_MODEL_UNPRICED`. |
| `EXPLAIN_MONTHLY_BUDGET_USD` | decimal (min 0) | `1.00` | Most an organization may spend on explanations per UTC month, in US dollars. `0` turns explanations off (`409 NOT_CONFIGURED`). |
| `USER_STATS_ENABLED` | boolean | `true` | Schedule the refresh of per-user daily statistics behind the Users page. Off, the page stops updating. |

### Evaluations

Evaluators score traces: heuristics run locally, LLM judges call a model through one of the organization's provider credentials under each evaluator's monthly budget, and webhooks ask your own service. Online evaluation samples recent spans every minute; experiments run a dataset through a prompt and score every item.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `EVALS_ENABLED` | boolean | `true` | Schedule online evaluation, which samples recent spans for every enabled evaluator once a minute. Off, evaluators run only inside experiments and test runs. |
| `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` | integer (min 1, max 10000) | `50` | Most evaluation runs one evaluator gets per one-minute sampling cycle. Spans past it are not sampled in that cycle. An LLM judge whose monthly budget is used up is not sampled at all. |
| `EXPERIMENT_MAX_ITEMS` | integer (min 1, max 100000) | `1000` | Ceiling for an experiment's `max_items`: the most dataset items one experiment run processes. |

### API request limits

A request that would otherwise wait on a busy database fails fast with `503` and `Retry-After` instead of queueing behind slow work. The worker, exports and deleting an organization or project are not subject to the statement limit.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `API_POOL_TIMEOUT_SECONDS` | number (min 1, max 60) | `5.0` | Seconds an API request waits for a connection from the main pool. Past it the request answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 5`. Blank keeps the default. |
| `API_STATEMENT_TIMEOUT_SECONDS` | number (min 1, max 300) | `10.0` | Longest one database statement of an API request may run. The database cancels it and the request answers `503 SERVICE_UNAVAILABLE` with `Retry-After: 5`. Blank keeps the default. |

### Connection pools

Idempotency keys and rate limiting each use a small pool of their own, so they cannot starve the main one. Change these only after reading the [scaling runbook](/docs/runbooks/scale/).

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `IDEMPOTENCY_POOL_SIZE` | integer (min 1) | `5` | Connections in the idempotency-key pool. |
| `IDEMPOTENCY_POOL_TIMEOUT_SECONDS` | number (greater than 0) | `5.0` | Seconds a request waits for an idempotency connection before it fails. |
| `RATE_LIMIT_POOL_SIZE` | integer (min 1) | `5` | Connections in the rate-limit pool. |
| `RATE_LIMIT_POOL_TIMEOUT_SECONDS` | number (greater than 0) | `0.25` | Seconds a rate-limit check waits for a connection. A check that cannot get one is skipped and the request goes through, so keep this short. |

### Monitoring and logging

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `METRICS_TOKEN` | secret | unset | Bearer token that protects `/metrics`. Unset makes `/metrics` answer `404`. The endpoint is served by the API directly, not through the web port. |
| `SENTRY_DSN` | string | unset | Sentry DSN for error reporting. Request bodies, cookies and query strings are never sent. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | string | unset | Base URL of an OTLP HTTP collector for Spanlight's own traces (spans go to `<endpoint>/v1/traces`; a value ending in `/traces` is used as it is, and a `/v1/otlp` base gets `/traces`). Unset turns self-tracing off. Request bodies, headers and query parameters are never recorded. |
| `OTEL_SERVICE_NAME` | string | `spanlight-api` | Service name on Spanlight's own spans. |
| `LOG_LEVEL` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_JSON` | boolean | `true` | Write logs as JSON lines. Turn off for human-readable development output. |

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

## Generating secrets

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"      # POSTGRES_PASSWORD, APP_DB_PASSWORD, SECRET_KEY, METRICS_TOKEN
echo "v1:$(openssl rand -base64 32)"                               # CREDENTIALS_KEYS
```

To change a secret on a running deployment, follow the [rotate secrets runbook](/docs/runbooks/rotate-secrets/).
