# API contract: deviations and clarifications (backend, Milestone 1)

The backend implements the API as first designed (that design is not published) without changing
any documented shape. This file is the reference for where the original design was silent or
ambiguous, and for the decisions the backend made. Items marked **Deviation** depart from the
original design; everything else is a clarification.

## Versioning

- The dashboard API is served under `/api/v1`, for example `GET /api/v1/projects/{project_id}/traces`.
  The earlier unversioned `/api/<resource>` paths no longer exist and return 404.
- Not part of the versioned surface, and unchanged: ingestion (`POST /v1/traces`, `POST /v1/otlp/traces`,
  versioned on its own), `/health/*`, `/metrics`, and the OpenAPI document and docs UI
  (`/api/openapi.json`, `/api/docs`).
- A breaking change ships as `/api/v2`. From then on `/api/v1` keeps working for at least 12 months
  and its responses carry a `Deprecation` header. Additive changes (new fields, endpoints, optional
  parameters) stay in `/api/v1`. See [ADR 5](decisions/0005-public-api-under-api-v1.md).
- The `route` label of `spanlight_http_requests_total` and `spanlight_http_request_duration_seconds`
  is the full route template, so it carries the prefix (for example
  `/api/v1/projects/{project_id}/traces`).

## Errors

- Every error is `application/problem+json`: `{type:"about:blank", title, status, detail, code, request_id, errors?}`.
  Every response also carries an `X-Request-ID` header (an incoming valid `X-Request-ID` is echoed back).
- Codes in use: `UNAUTHORIZED` (401), `INVALID_CREDENTIALS` (401, login), `KEY_EXPIRED` (401, an API key
  past its `expires_at`), `TOKEN_EXPIRED` (401, a personal access token past its `expires_at`),
  `FORBIDDEN` (403), `KEY_SCOPE` (403, an API key used outside its scopes),
  `TOKEN_SCOPE` (403, a read-only personal access token used to change something),
  `SESSION_REQUIRED` (403, a personal access token used on a route that needs a signed-in session),
  `CSRF_FAILED` (403), `ORIGIN_NOT_ALLOWED` (403), `NOT_FOUND` (404), `EMAIL_TAKEN` (409),
  `EMAIL_ALREADY_VERIFIED` (409), `EMAIL_UNVERIFIED` (409, an action needs a verified email address while
  email is configured), `LAST_OWNER` (409), `NOT_CONFIGURED` (409, a request needs an
  optional integration the operator has not set up; `detail` names the setting to change),
  `LAST_SIGN_IN_METHOD` (409, unlinking the only way to sign in),
  `TOTP_ALREADY_ENABLED` / `TOTP_NOT_ENABLED` (409, two-factor setup state),
  `TWO_FACTOR_NOT_ENABLED` (409, an owner requires two-factor for the org without having it),
  `TWO_FACTOR_REQUIRED` (403, the org requires two-factor and the caller has not turned it on),
  `INVALID_TOTP_CODE` (422 when enabling or disabling, 401 when signing in),
  `TOTP_CHALLENGE_INVALID` (401, the second step of signing in),
  `CONFIRMATION_MISMATCH` (422, a deletion whose typed `confirm` is not the slug of what it deletes),
  `IDEMPOTENCY_MISMATCH` (422, an `Idempotency-Key` first used for a different request),
  `IDEMPOTENCY_IN_PROGRESS` (409, the first request with that key has not answered yet),
  `SLUG_UNAVAILABLE` (409), `PAYLOAD_TOO_LARGE` (413),
  `UNSUPPORTED_MEDIA_TYPE` (415), `VALIDATION_ERROR` (422, with `errors[].field`),
  `RATE_LIMITED` (429, with `Retry-After`), `INVALID_JSON` / `INVALID_OTLP` / `BAD_REQUEST` (400),
  `INTERNAL_ERROR` (500), `SERVICE_UNAVAILABLE` (503, with `Retry-After`, the database could not take or
  finish the request in time).
- The codes of prompts, scores, datasets, review queues, evaluators and experiments are listed in the section
  "Prompts, scores, datasets, evaluators and experiments" below.
- Validation `errors[].field` is the dotted path without the `body.`/`query.` prefix (e.g. `password`).
- **NUL in a text parameter is a 422** (added 2026-10-10). Postgres text cannot hold NUL, so a query or path value with
  one could not be bound. The insights, releases and users parameters refuse it as `422 VALIDATION_ERROR` with the
  field named; any other text value that reaches the database with NUL also answers `422 VALIDATION_ERROR` (without
  `errors`) instead of a 500.
- **A busy database is a 503, not a 500** (added 2026-10-09). A request answers `503 SERVICE_UNAVAILABLE` with
  `Retry-After: 5` in two cases: it waited longer than `API_POOL_TIMEOUT_SECONDS` (default 5) for a database
  connection, or one of its statements ran longer than `API_STATEMENT_TIMEOUT_SECONDS` (default 10) and the
  database cancelled it. Before this, the first case waited 30 seconds and then answered `500 INTERNAL_ERROR`, and
  the second ran until the client gave up, holding its connection the whole time. The server logs each as a
  warning (`db_pool_timeout`, `db_statement_timeout`) and not as an unhandled exception. Clients may retry after
  the delay; the Python SDK already does for every `5xx`. Both limits apply to the database session of every
  `/api` and ingestion request, with two exceptions: deleting an organization or a project is not subject to the statement limit
  (the foreign keys remove all its spans in one statement, which on a large project takes longer than any
  request limit), and the work that runs on its own sessions has no statement limit: the worker, exports, the
  audit-log CSV stream (`GET /api/v1/orgs/{org_id}/audit/export.csv`) and the idempotency and rate-limit pools (the
  last two have their own pool settings). Any other database failure stays a `500 INTERNAL_ERROR`.
- **Request body limit:** every `/api/v1` request body is limited to **1 MiB**. Over the limit is `413
  PAYLOAD_TOO_LARGE`, answered without reading the body when `Content-Length` already declares too much, and
  while reading when the body is chunked or understates its length. Ingestion (`/v1/traces`,
  `/v1/otlp/traces`, `/v1/scores`) is outside this limit and keeps its own 5 MB. The dataset upload route
  (`POST …/datasets/{dataset_id}/items/upload`) has its own limit of 5 MB of file plus 64 KB of multipart framing.

## Auth, CSRF, sessions

- `spl_csrf` is a signed token bound to the session; the SPA echoes the cookie value in `X-CSRF-Token`.
  `GET /api/v1/auth/me` re-issues `spl_csrf` if it is missing or stale, so the SPA can recover.
- State-changing `/api/` requests (every version) **without an `Origin` header are rejected** (403 `ORIGIN_NOT_ALLOWED`),
  not only those with a foreign Origin. Browsers always send Origin on POST/PUT/PATCH/DELETE. A request that
  carries `Authorization: Bearer …` is exempt from this check and from CSRF (see API key scopes, expiry and
  bearer requests); other `Authorization` schemes are not.
- CSRF is required on every authenticated unsafe method, including `POST /api/v1/auth/logout` and
  `POST /api/v1/invites/accept`. Exempt (Origin check only): `signup`, `login`, `POST /api/v1/demo/session`,
  `POST /api/v1/auth/email/verify/confirm`, `POST /api/v1/auth/password/forgot`,
  `POST /api/v1/auth/password/reset`, `POST /api/v1/auth/totp/verify`.
- `POST /api/v1/auth/signup` is limited to **10 attempts per IP per hour**; the 11th is 429 `RATE_LIMITED`
  with `Retry-After`. Every attempt counts, including one refused with `409 EMAIL_TAKEN`, so probing for
  registered addresses is limited too.
- Login throttle: once an email has **5** failures (or an IP **20**) within 15 minutes, further attempts
  return 429 with `Retry-After` — i.e. the 6th attempt is blocked. Throttled attempts are not recorded.
  Wrong two-factor codes count as failed sign-ins too (see Two-factor authentication).
- `POST /api/v1/demo/session` → **200 `User`** and sets the same cookies as login (`Max-Age` 3600).
  404 when the demo is disabled; 429 after 10 per IP per hour.
- `DELETE /api/v1/auth/sessions/{id}` returns 404 for sessions that are not the caller's.
- **Addition:** `DELETE /api/v1/auth/sessions` → 204 signs out everywhere else: it deletes every session of
  the caller except the one making the request, which keeps working. It needs a session and the CSRF header like
  any authenticated unsafe method, and it is a no-op (still 204) when there are no other sessions. Other users'
  sessions are never touched.
- **The shared demo account's sessions are not manageable.** Every demo visitor is the same user, so their
  sessions belong to other people. For the demo user, `GET /api/v1/auth/sessions` lists only the caller's own
  session (so other visitors' addresses and browsers are not shown), and `DELETE /api/v1/auth/sessions` and
  `DELETE /api/v1/auth/sessions/{id}` answer `403 FORBIDDEN` without ending anything. A visitor can still
  `POST /api/v1/auth/logout` to end their own session.

## Orgs, members, invites

- `PATCH /api/v1/orgs/{org_id}/members/{user_id}` → 200 `{user: User, role, created_at}` (same shape as a
  member list item).
- Only owners may grant the owner role, change an owner's role, remove an owner, or invite as owner;
  admins get 403 for those.
- `DELETE …/members/{own user id}` lets any member **leave** the org (still subject to `LAST_OWNER`).
- Accepting an invite as an existing member keeps the existing role (never downgrades).
- Invalid, used, expired or revoked invite tokens → 404.
- **Addition:** `GET /api/v1/invites/preview?token=…` → 200 `{org: {id, name, slug}, role, expires_at}`,
  so the invite page can show the organization and role **before** the user accepts. It requires a
  signed-in session (401 otherwise), changes nothing (no audit event, the invite stays usable) and
  returns the same 404 `NOT_FOUND` for unknown, used, expired and revoked tokens. The token travels
  in the query string, like the `/invite/{token}` page URL it comes from.
- **Addition (invite emails, 2026-10-08):** `POST /api/v1/orgs/{org_id}/invites` takes an optional
  `email` (a valid address, else 422 `VALIDATION_ERROR`) next to `role`. The create response and
  `GET /api/v1/orgs/{org_id}/invites` items gain `email`, which is `null` for an invite created without
  one. The response still returns `url`, whether or not anything was mailed.
  - **Mailing.** With an `email` and a configured email sender, the invitation is also queued for
    delivery. The invite, its `invite.create` audit event and the queued email are one transaction.
    The email names the inviter (the caller's name), the organization and the role, and carries the
    same `url`. Without an `email`, or when email is not configured, nothing is queued and the invite is
    created as before; the address, if given, is still stored and listed. The response does not say
    whether the email was queued, so a client that needs to know checks whether email is configured.
  - **Limit.** At most **20 mailed invites per organization per hour**, counted only for invites that
    are mailed (link-only invites and invites created while email is not configured do not count).
    The 21st gets 429 `RATE_LIMITED` with `Retry-After` (whole seconds, rounded up) and **no invite is
    created**. Refused requests are not recorded, so retrying early does not extend the wait. The
    counts live in Postgres (`throttle_events`), so they hold across API replicas. The limit exists
    because anyone can sign up, create an organization and invite arbitrary addresses; unlimited, that
    would let strangers send mail from the instance's sender address.
  - **Accepting is unchanged.** Mailing does not tie an invite to the address it was sent to: any
    signed-in holder of the link can accept it, as for a link shared by hand. The link keeps its
    `{APP_BASE_URL}/invite/<token>` shape. Because the token is in the path, it can appear in access
    logs and `Referer` headers; unlike the verification and reset links, it is not in the URL fragment.
- **Addition (2026-10-08):** `PATCH /api/v1/orgs/{org_id}` → 200 `Org` takes `{"require_2fa": bool}` (see
  Two-factor authentication) and, since 2026-10-09, `name` (see Organization and project administration).
  `Org` (in `memberships[].org`, `POST /orgs`, this response and `GET /orgs/{id}`) gains a `require_2fa`
  boolean, false by default.
- Audit actions: `org.create, org.update, member.add, member.role_change, member.remove, invite.create,
  invite.revoke, invite.accept, project.create, project.update, project.delete, key.create, key.revoke,
  user.password_reset, user.oauth_link, user.oauth_unlink, user.totp_enable, user.totp_disable`.
  `actor` is null for CLI actions.
- Internal: a permission `org:read` (held by every role) guards org-level reads; it does not change
  which roles can do what.

## Projects, keys

- Creating a project requires `project:write` (admin/owner). Listing keys requires `project:read`.
- `prefix` is the displayable start of the key, `spl_live_<12 chars>`; the full key is
  `<prefix>_<32 chars>`. `secret` appears only in the 201 response.
- `last_used_at` is updated at most once per minute, by ingestion and by reads alike.
- Keys have scopes and an optional expiry; see API key scopes, expiry and bearer requests.
- Projects can be deleted (`DELETE /api/v1/projects/{project_id}`); see Organization and project
  administration.

## Traces, sessions, metrics

- Non-members get 404 for every project route; members lacking a permission get 403.
- `from`/`to` default to the last 24 h; `from >= to` or a window > 90 days → 422.
- Sessions: `last_at` is the latest trace `ended_at`; ordering and cursors use it (desc).
- Sessions `environment` (addition): `GET …/sessions?environment=` keeps only traces of that environment before the
  traces are grouped, exactly as the trace list does. A session that spans environments is listed with the
  `trace_count`, `cost_usd`, `error_count`, `first_at` and `last_at` of its matching traces only, and `last_at`
  (so the order and the cursor) follows those traces. A session with no trace in the environment is not listed.
- KPI definitions (overview, timeseries, models):
  - `llm_calls`, `errors`/`error_rate`, `p50_ms`/`p95_ms` (`percentile_cont`) and `unpriced_calls` are
    computed over spans of kind `llm` started in the window.
  - `cost_usd` and token totals are over **all** spans in the window (matching trace rollups);
    `cost_usd` is `null` when nothing in the window was priced. `error_rate`/percentiles are `null`
    when there are no LLM calls.
  - `traces` counts traces started in the window. `environment` filters by the trace's environment.
  - Timeseries buckets are gapless (empty buckets have zero counts and `null` cost/p95), in UTC.
- Metrics source and `approximate`: windows of 24 h or less read the raw spans (exact percentiles);
  longer windows read the hourly rollups (hours whose `bucket_start` is in `[floor_hour(from), to)`,
  so the first and last hour count in full) and estimate p50/p95 from merged latency histograms.
  The overview's previous period follows the same rule as the current one. `overview` carries a
  top-level `approximate: bool`; `timeseries` and `models` stay plain lists (an envelope would break
  clients), so each item carries the same `approximate: bool`. Rollup reads can lag up to 5 minutes.
- Trace `status=ok|error` filters on `error_count = 0 | > 0`. `q` matches the trace name
  (case-insensitive substring) or an exact trace id.
- Money is a decimal string with up to 8 fractional digits, e.g. `"0.00021000"`.

## Ingestion (`POST /v1/traces`)

- Both ingestion routes need a key with the `ingest:write` scope (403 `KEY_SCOPE` otherwise). The scope is checked
  before the per-key rate limit, so a key that may not ingest spends nothing from it.
- **Token convention (shared with the SDK):** `usage.input_tokens` is the total prompt size
  *including* cached tokens; `usage.cached_tokens` is the cached subset. Cost =
  `(input - cached) × input_rate + cached × cached_rate + output × output_rate`; with no published
  cached rate, cached tokens bill at the input rate. Cache writes are not priced separately.
  `cached_tokens > input_tokens` rejects the span.
- Cost is `null` (never 0) for unknown models or when input/output tokens are missing; such
  LLM spans set the trace's `has_unpriced`.
- **Deviation — model matching:** a price pattern matches the model exactly, or followed by a
  snapshot suffix (`-YYYYMMDD`, `-YYYY-MM-DD`, `-latest`); longest pattern wins. Plain prefix
  matching (as in the original design) would price e.g. `gpt-4.1-nano` as `gpt-4.1`. Matching is
  case-sensitive (since 2026-10-13, see "Owner decisions 2026-10-13"); the provider is compared
  case-insensitively, and when `provider` is sent it must match the price's provider.
- Unknown span fields are ignored (forward compatible) rather than rejected.
- Per-span rejections (`rejected[].reason` is `"<field>: <message>"`): schema errors; `end_time`
  before `start_time`; `start_time` older than 90 days; `end_time` more than 10 minutes in the
  future; a span parenting itself; attributes over 64 KB; all-zero ids. Timestamps must be ISO-8601
  **with an offset** or integer unix nanoseconds. Ids are lowercased.
- A value the database cannot store is rejected with the span, never by failing the batch: a
  timestamp whose offset moves it outside years 1–9999, a `time_to_first_token_ms` larger than a
  double, and a cost of $1,000,000 or more (`usage: the cost at this model's price is too large to
  store`; only an organization's price override can price a call that high). Any other unexpected
  failure while preparing a span rejects that span alone with `span: could not be processed`; it is
  logged by type and origin and counted in `spanlight_span_normalization_failures_total`.
- A trace's `cost_usd` is `null` when the sum of its spans' costs reaches $1,000,000, the most the
  column holds; the spans keep their own costs.
- A `span_id` repeated within one batch keeps the **last** occurrence; earlier ones are reported in
  `rejected` with reason `duplicate span_id in batch…`.
- Trace fields: last non-null wins (within a batch and across batches); `null` never overwrites.
  `tags` are unioned. Rollups are always recomputed from stored spans, so re-sending is idempotent.
- **Replaying a batch.** Sending the same spans again leaves every column of their rows and of their traces
  as it was, except `ingested_at`, which moves to the time of the latest send, as long as prices have not
  changed. The spans are priced again with the prices in force at that moment (the price table and the
  organization's overrides), so a price change between two sends changes a span's `cost_usd` and
  `pricing_version` and the trace's totals with it.
- Payloads: secrets are redacted first, then `input`/`output` over 32 KB are replaced by a **string**
  holding the first 32 KB of their JSON text, with `truncated: true`.
- The body may be `Content-Encoding: gzip` or `deflate`; the 5 MB limit applies after decompression.
  NaN/Infinity and NUL characters are rejected/stripped.
  JSON nested more than 64 levels deep, or holding a number of more than 4300 digits or one past
  a double (`1e999`, which would be an infinity), is `400 INVALID_JSON`.

## OTLP (`POST /v1/otlp/traces`)

- Model: `gen_ai.response.model` is preferred over `gen_ai.request.model` (it is the billed snapshot).
- Kind: `llm` if any `gen_ai.*` attribute; `http` for client spans with `http.*`/`url.*`
  attributes; `retrieval` with `db.*`; otherwise `other`.
- Trace name: the root span's name (falling back to `service.name`); non-root spans do not set it.
- Legacy `gen_ai.usage.prompt_tokens`/`completion_tokens` are accepted. Content attributes are parsed
  as JSON when they are JSON strings and are removed from `attributes`; `service.name` is copied into
  `attributes`.
- A double that is NaN or infinite (protobuf, or the OTLP/JSON strings `"NaN"`, `"Infinity"`,
  `"-Infinity"`) is stored as that string, since `jsonb` has no such numbers. Inside JSON-encoded
  content, NaN, Infinity and a number past a double (`1e999`) become such a string the same way.
  A malformed `intValue`/`doubleValue`, or a value where a JSON object belongs, is
  `400 INVALID_OTLP`.
- An OTLP/JSON body in which a number is written past a double (`"doubleValue": 1e999`, a number literal and
  not the string `"Infinity"`) is not usable JSON for the server: the whole request is `400 INVALID_JSON`, as on
  `POST /v1/traces`, and no span of it is stored.
- JSON ids may be hex (as in the original design) or base64. Response: `{}` or
  `{"partialSuccess": {"rejectedSpans": "<n>", "errorMessage": "…"}}`; protobuf mirrors this.

## Health and metrics

- `/metrics` returns 404 when `METRICS_TOKEN` is unset, 401 for a wrong/missing bearer.
- `/health/ready` reports `migrations: "pending"` (503) when the DB is not at the Alembic head.
- `/health/ready` reports `gateway_spans_lost`: the gateway spans this process answered a call for but could
  not store since it started (the write failed after up to four tries, a span for a deleted project being not retried;
  its payloads could not be prepared; ingestion refused it; or the write backlog was full). It is
  per process (the standalone gateway reports its own), resets on restart, and never makes the probe `503`; the
  same spans are in `spanlight_gateway_record_failures_total`.
- `alert_evaluation_lag_s` is the age of the newest finished evaluation job. It is `null` before the first one, with
  `ALERTS_EVALUATION_ENABLED` off, and also once the cleanup job has deleted every finished evaluation job (finished
  jobs are kept 7 days). A worker that has been stopped for more than a week therefore reads `null`, not a large
  number; the worker heartbeat age is the signal that stays.

## Worker / demo

- **The demo job's LLM calls go through the gateway, in process.** It calls `execute` directly (no
  HTTP hop) with the demo project's own gateway key `demo` (environment `demo`, default tag `demo`),
  so its `llm` spans carry `spanlight.gateway.key_id` like any keyed call. That key has no rate
  limits, cache TTL or fault profile, and the in-process call checks no limits; its route `demo`
  sends every call once to the credential `demo-anthropic` (one attempt, no fallback, 20 s). The job
  creates all three on first run and seals `ANTHROPIC_API_KEY` into the credential, sealing it again
  (a `credential.rotate`) only when the configured key differs from the stored one. Those audit
  events have no actor. The root `chain` span and the `faq-lookup` retrieval span are still written
  by calling the ingestion pipeline function, into the same trace; the gateway spans are named
  `messages <model>`, as every gateway span is.
- The demo job skips (`skipped_not_configured`, reason "credentials key not configured") without
  `CREDENTIALS_KEYS`, as it does without `ANTHROPIC_API_KEY`. An existing `demo` route and key are
  reused as they are, so before any provider call the job checks them: one attempt, no fallback,
  a single target on `demo-anthropic`, the key on that route, no cache TTL and no fault profile.
  Anything else skips the run (`skipped_not_configured`, reason "demo gateway configuration
  changed") without calling the provider.
- The demo job also skips (with a warning) if any billed demo LLM call this month is unpriced, since
  the budget could not be enforced. It runs at most one attempt per period (no paid retries).
- `cleanup_sessions` also prunes login attempts and throttle events older than a day, email tokens that
  are used or expired and older than a day, finished jobs older than 7 days, and idempotency keys past
  their 24 hours.

## Trace summaries: `error_message` (added 2026-10-08)

`TraceSummary` (trace list items and trace detail) gains `error_message: string | null` — the
status message of the earliest failed span (ties broken by span id), truncated to 500 characters.
`null` when no span failed or the failed span sent no message. Used by the Overview "Recent errors"
card and the trace list. Computed with a correlated subquery on the spans primary key.

## Price catalogue (added 2026-10-09)

- `GET /api/v1/prices` lists every row of the price table ordered by provider, `model_pattern` and
  `effective_from`; money is a decimal string. Any signed-in user may read it (session or access
  token); API keys get 403 `KEY_SCOPE`. There is no write endpoint: prices change through
  `spanlight sync-prices`.
- `GET /api/v1/projects/{project_id}/unpriced-models` needs `project:read` (an API key with
  `traces:read` may read its own project) and takes the usual `from`/`to` window. It groups LLM
  spans started in the window by provider and model, counting those with a model, both token
  counts and a NULL cost: the model matched no price at ingestion. Spans missing a model or a
  token count are NULL-cost for another reason and are not listed, so the list can be shorter
  than the overview's `unpriced_calls`. Most-called first, at most 100 models.

## Price overrides (added 2026-10-09)

An organization can set its own rates for a model. The routes are under
`/api/v1/orgs/{org_id}/price-overrides`: `GET` (`org:read`) lists them ordered by provider,
`model_pattern` and `effective_from`, `POST` (`gateway:write`, owners and admins) adds one and
returns 201, `DELETE /{override_id}` returns 204. Non-members get 404, and so does an override of
another organization.

- Body: `provider`, `model_pattern`, `input_per_mtok`, `output_per_mtok`, optional
  `cached_input_per_mtok` (USD per million tokens, not negative, below 1,000,000, at most six
  decimals) and optional `effective_from` (defaults to now; a time without an offset is UTC).
  `provider` and `model_pattern` are trimmed and stored lower-case, as in the price table.
- The same provider, pattern and `effective_from` twice in one organization is
  `409 PRICE_OVERRIDE_EXISTS`.
- Matching follows the seed prices: a pattern matches a model exactly or with a snapshot suffix
  (`-YYYYMMDD`, `-YYYY-MM-DD`, `-latest`), never by prefix. The longest matching pattern wins
  whatever its source; for equal patterns an override beats the seed row. An override applies to a
  span whose start time is at or after its `effective_from`.
- A span priced by an override stores `pricing_version` `override:<id>`. Spans already stored keep
  the cost they were priced with; adding or deleting an override affects spans ingested afterwards.
  Replaying a span (sending the same span id again through native ingestion) re-prices it with the
  overrides in force at replay time. A span the gateway recorded is never replayed over (see Span
  attribution under Gateway keys), so its cost stays as the gateway priced it.
- Audit events `price_override.create` and `price_override.delete` have target type
  `price_override` and the provider and pattern in their metadata.

## Email verification (added 2026-10-08)

Verification is soft: an unverified user uses the product normally. Two actions need a verified email address when email is configured (`409 EMAIL_UNVERIFIED`): turning on two-factor authentication and linking a sign-in provider.

- `User` gains `email_verified: boolean`, true once the owner of the address has proven it. It is on
  every response that embeds a user: signup, login, `GET /api/v1/auth/me` (as `user.email_verified`), members, API key creators and audit actors.
- `GET /api/v1/auth/me` gains a top-level **`email_verification_required`** boolean, next to `has_password`:
  true only when the server can send email, the caller's address is not yet verified and the caller is not the
  shared demo account (which is never asked and cannot receive email). It exists so the dashboard knows whether
  to ask for verification at all, since on a server without email no link can arrive.
- `POST /api/v1/auth/email/verify/request` (session and CSRF) → **202 `{"status": "accepted"}`** and
  queues a verification email for the signed-in user. The body is the `AcceptedOut` shape, meant for
  any request whose work is finished later by the worker. Checked in this order:
  1. 409 `NOT_CONFIGURED` when email is not configured. `detail` names `EMAIL_PROVIDER` and
     `EMAIL_CONSOLE_FILE`; the `console` provider counts as configured only when `EMAIL_CONSOLE_FILE`
     is set, since logging an address delivers nothing.
  2. 403 `FORBIDDEN` for the shared demo user. Everyone who starts a demo session is signed in as that
     one anonymous account, so no visitor may make the server send mail to its address.
  3. 409 `EMAIL_ALREADY_VERIFIED`.
  4. 429 `RATE_LIMITED` with `Retry-After` (whole seconds, rounded up) after **3 requests per user per
     hour**. The email sent at signup is not counted, and refused requests are not recorded, so
     retrying early does not extend the wait. The count lives in Postgres, so it holds across API
     replicas.
- `POST /api/v1/auth/email/verify/confirm` with `{"token"}` → **200 `User`**. It needs no session,
  because the link is often opened in a browser that is not signed in; the Origin check still applies
  and CSRF does not (there is no session to bind it to).
  - **404 `NOT_FOUND`, the same response for every bad token**: unknown, already used, expired, or
    issued for another purpose (a password-reset token). Nothing tells a caller which. A token
    presented to the wrong route is not spent.
  - Confirming an address that is already verified (with a second valid token) returns 200 and keeps
    the original verification time.
- **The link** is `{APP_BASE_URL}/verify-email#token=<token>`. The token is in the URL fragment, which
  browsers send neither to the server nor in a `Referer` header, so it stays out of access logs; the
  page reads the fragment and posts it to the confirm route.
- **The token** is 32 random bytes in URL-safe base64 (43 characters), stored only as its SHA-256,
  single use, and valid for 24 hours. Concurrent confirmations with one token succeed exactly once.
- Signup queues the verification email in the signup transaction when email is configured, so the
  account, its token and the queued email commit together. Without email configured nothing is
  queued and the user stays unverified.

## Password reset (added 2026-10-08)

Both routes need no session, because the person has forgotten their password. The Origin check still
applies and CSRF does not (there is no session to bind it to). Neither sets a cookie.

- `POST /api/v1/auth/password/forgot` with `{"email"}` → **202 `{"status": "accepted"}`** (`AcceptedOut`).
  - **The answer is the same for every address.** A known address, an unknown one and the demo user's
    get the same status and the same body; only a known, non-demo address also gets a token and a
    queued email. Unknown addresses queue nothing. Both kinds are counted by the limits below first,
    so hitting a limit says nothing about whether an account exists.
  - 409 `NOT_CONFIGURED` when email is not configured, with the same `detail` as the verification
    request. It is the operator's state, so it is the same for every address and is checked before
    the limits (such a request is not counted).
  - 429 `RATE_LIMITED` with `Retry-After` (whole seconds, rounded up) after **3 requests per email
    address per 15 minutes**, or **10 per client IP per 15 minutes**. The email address is compared
    case-insensitively. The email limit is checked first, then the IP limit, always in that order.
    Refused requests are not recorded, so retrying early does not extend the wait. The counts live in
    Postgres (`throttle_events`), so they hold across API replicas. The sign-in throttle is separate
    and unchanged: it still counts failed sign-ins in `login_attempts`.
  - Asking again retires the reset links sent before: **at most one reset link is live per user**.
    Verification links are not affected.
  - Validation: `email` must be a valid address (422 `VALIDATION_ERROR`).
- `POST /api/v1/auth/password/reset` with `{"token", "password"}` → **204**, no body. `password` is
  10–256 characters, the same rule as signup.
  - **404 `NOT_FOUND`, the same response for every bad token**: unknown, already used, expired, or
    issued for another purpose (a verification token). A token presented to the wrong route is not
    spent. A password that fails validation (422) does not spend the token either, so the link can be
    used again with a better one.
  - A successful reset is one transaction. It sets the new password hash; **ends every session** of
    the user; sets `email_verified_at` if it was unset, since the link reached the inbox (an earlier
    verification time is kept); marks the user's other unused reset links as used; and writes a
    `user.password_reset` audit event, with `actor` the user, `target` the user and `metadata`
    `{"via": "email"}`, to each org the user belongs to.
  - **No session survives the change.** A sign-in that was verifying the old password while the reset
    committed either is refused (401 `INVALID_CREDENTIALS`) or has the session it created ended by the
    reset; it never keeps one. The same holds for `spanlight reset-password`.
  - **It does not sign the user in.** The person signs in afterwards with the new password.
- **The link** is `{APP_BASE_URL}/reset-password#token=<token>`, with the token in the URL fragment for
  the same reason as the verification link. The page reads the fragment and posts it to the reset route.
- **The token** has the same format as the verification token (43 characters, stored only as its
  SHA-256, single use) and is valid for **1 hour**. Concurrent resets with one token succeed exactly once.
- **The demo user** cannot be reset by email: `forgot` treats its address like an unknown one, and it
  sends nothing.
- The reset email body, which holds the link, is kept in the notification outbox only until the row is
  settled. Once it is sent, or has failed for good, the stored payload is reduced to the subject.

## Sign in with GitHub and Google (added 2026-10-08)

A provider is **configured** when both its `OAUTH_<PROVIDER>_CLIENT_ID` and `OAUTH_<PROVIDER>_CLIENT_SECRET`
are set (a blank value counts as unset). Setting only one of a pair stops the app at startup, and the
message names the two variables, not their values. The callback URL to register with the provider is
`{APP_BASE_URL}/api/v1/auth/oauth/{provider}/callback`.

| Route | Auth | Behaviour |
|---|---|---|
| `GET /api/v1/auth/oauth/providers` | none | `[{"provider": "github"}, {"provider": "google"}]`, configured providers only, in that order |
| `GET /api/v1/auth/oauth/identities` | session | `[{"provider", "email", "created_at", "last_used_at"}]`, the caller's linked accounts, oldest first |
| `GET /api/v1/auth/oauth/{provider}/start?next=&intent=sign_in\|link` | none (`link` needs a session) | `302` to the provider; `404 NOT_FOUND` for an unknown or unconfigured provider; `401 UNAUTHORIZED` for `intent=link` without a session; for `intent=link` also `403 FORBIDDEN` for the demo account and `409 EMAIL_UNVERIFIED` for an unverified account (see Linking); `422` for another `intent` |
| `GET /api/v1/auth/oauth/{provider}/callback?code&state` | the `spl_oauth` cookie | `302`, always (see below); `404` for an unconfigured provider |
| `DELETE /api/v1/auth/oauth/{provider}` | session + CSRF | `204`; `404 NOT_FOUND` if that provider is not linked; `409 LAST_SIGN_IN_METHOD` if the user has no password and no other linked provider |

- **`start` and `callback` are browser navigations, so they never answer with problem+json** once the
  provider is known. A failed callback is a `302` to `{APP_BASE_URL}/login?error=<CODE>`, with `<CODE>` one of
  `OAUTH_STATE`, `OAUTH_PROVIDER_ERROR`, `EMAIL_UNVERIFIED_AT_PROVIDER`, `ACCOUNT_EMAIL_UNVERIFIED` or
  `OAUTH_ALREADY_LINKED`. Every query parameter of the callback is optional, so a malformed callback gets
  this redirect rather than a `422`. All of these responses carry `Cache-Control: no-store`.
- **The state cookie `spl_oauth`** is set by `start` and cleared by every callback, whatever its outcome. It
  is httpOnly, `SameSite=Lax`, `Secure` when `APP_BASE_URL` is https, scoped to `Path=/api/v1/auth/oauth`
  and lives 10 minutes. Its value is signed with `SECRET_KEY` (HMAC-SHA256) and holds the `state` sent to
  the provider, the PKCE verifier, the provider, the intent, `next` and, for a link, the user who started
  it. The callback needs the cookie to be present, correctly signed, unexpired and for the same provider,
  and its `state` to equal the `state` query parameter (compared in constant time); otherwise it redirects
  with `OAUTH_STATE`, before anything is sent to the provider. This is what stops a login CSRF: a callback
  URL built from someone else's `code` has no matching cookie in the victim's browser.
- **`next`** must be a path that starts with exactly one `/`. Anything else (`//host`, `/\host`, a scheme,
  a control character anywhere, more than 2048 characters) becomes `/`. The default is `/` for a sign-in and
  `/settings/security` for a link. The redirect is always `{APP_BASE_URL}{next}`.
- **The provider exchange** uses the authorization-code flow with PKCE (`S256`). The access token is used
  for the profile calls only and is neither stored nor logged. Calls time out after 10 seconds. A refused
  code, an unreachable provider, a bad status or a malformed answer is `OAUTH_PROVIDER_ERROR`, as is the
  user declining at the provider (`error=access_denied`). Scopes: GitHub `read:user user:email`, Google
  `openid email profile`.
- **The profile**: GitHub's `/user` (id, name, login) plus `/user/emails` (the primary address and its
  `verified` flag); Google's userinfo (`sub`, `email`, `email_verified`, `name`). An email counts as
  verified only when the provider says so. A provider that reports no primary address gives no email.

**Resolution of a sign-in**, in this order:

1. An identity with this `(provider, subject)` exists: that user signs in. The subject is the provider's
   stable account id; a changed or unverified email does not matter.
2. Otherwise, if a user has the same email: the provider must say it is verified (else
   `EMAIL_UNVERIFIED_AT_PROVIDER`) and the local account's email must be verified (else
   `ACCOUNT_EMAIL_UNVERIFIED`); with both, the identity is linked and the user signs in. A user that already
   has a different account of the same provider linked gets `OAUTH_ALREADY_LINKED`. The shared demo account
   is never matched.
3. Otherwise, if the provider says the email is verified, a **new user** is created with no password,
   `email_verified_at` set, and the provider's name (the part of the email before `@` if there is none; at
   most 100 characters). If the email is not verified, or there is none, **no account is created**
   (`EMAIL_UNVERIFIED_AT_PROVIDER`): GitHub lets anyone attach an unverified address to their account, and an
   account created for it would keep that GitHub identity after the real owner of the address recovered it.

On success the callback creates a session exactly like password login (same cookies, same lifetimes) and
redirects to `{APP_BASE_URL}{next}`. Concurrent first sign-ins for one account create one user.

For a user with **two-factor authentication** on, the callback creates no session and sets no session
cookie. It redirects to `{APP_BASE_URL}/login/two-factor#challenge=<challenge>` instead (the challenge is in
the fragment, so no server sees it), and the page finishes with `POST /api/v1/auth/totp/verify`. `next` is
not carried over. What the callback wrote before that point stays: a new user, or an identity linked
because both emails are verified. That identity is the user's own provider account, and every sign-in
through it still needs the second factor.

**Linking** (`intent=link`): the identity attaches to the signed-in user, who must be the user who started
the flow, else `OAUTH_STATE`. The provider's email is not checked, since the person already proved who they
are with their session and sign-in never goes by that email. An account already linked to someone else, or a
different account of the same provider than the one the user already linked, is `OAUTH_ALREADY_LINKED`.
Linking the account that is already linked is a no-op.

- **Only verified accounts can link, when email is configured.** Anyone can register an address that is not
  theirs, and a provider attached to that unproven account would keep a way in after the address's real
  owner recovered the account. So with email configured (`EMAIL_PROVIDER` set up, see `NOT_CONFIGURED`),
  `start?intent=link` answers **409 `EMAIL_UNVERIFIED`** for a user whose email is not verified, and the
  callback checks again and redirects with `ACCOUNT_EMAIL_UNVERIFIED`, linking nothing. Without email nobody
  can verify an address, and the email recovery that makes this dangerous cannot run, so linking stays
  allowed for unverified accounts.
- **The demo account cannot link**: `start?intent=link` answers **403 `FORBIDDEN`** for it, whether or not
  email is configured, and the callback refuses with `ACCOUNT_EMAIL_UNVERIFIED`. It is shared and
  anonymous, so a visitor's provider account would otherwise stay bound to it, show up in its identity
  list for later visitors and put the visitor's IP in the demo org's audit log.
- **Ruling: a link returns to `next`.** The login page sends signed-in users away, which would lose an error
  shown there, so for `intent=link` both success and failure redirect to `{APP_BASE_URL}{next}`; a failure
  appends `error=<CODE>` to the query string (`next` keeps its own query and fragment).
- **Unlinking** takes a lock on the user, so two simultaneous unlinks cannot remove every way to sign in.
  It works whether or not the provider is still configured.
- **Audit**: `user.oauth_link` (`metadata`: `provider`, and `via` of `email_match` or `link`) and
  `user.oauth_unlink` (`metadata`: `provider`) are written to each org the user belongs to, with `actor` and
  `target` the user. Creating a user through a provider writes nothing, since the user has no org yet.

**Users without a password**
- `GET /api/v1/auth/me` gains a top-level **`has_password`** boolean: false for a user who signed up with a
  provider and never set a password, and for the shared demo account. It is not on `UserOut`, which other
  people's profiles are also shown through.
- `POST /api/v1/auth/login` for such a user answers exactly as for an unknown address: **401
  `INVALID_CREDENTIALS`**, after a dummy password verification so it takes as long, and counted as a failed
  attempt.
- Such a user can set a password with the password reset flow; the email proves the inbox.

## Password reset and linked providers (added 2026-10-08)

A password reset that **verifies a previously unverified email** (the first time the address is proven) also
**removes every sign-in provider linked to that user**, in the same transaction, and writes a
`user.oauth_unlink` audit event per removed provider to each org the user belongs to, with `metadata`
`{"provider", "reason": "password_reset"}`. Until then the address was unproven, so whoever registered it
could have attached a provider that would otherwise stay on the account after the owner recovered it. The
owner can link their own providers again afterwards. A reset for an account whose email was already
verified leaves its providers alone. `spanlight reset-password` (the admin CLI) changes neither.

## Two-factor authentication (added 2026-10-08)

Time-based one-time passwords (RFC 6238: HMAC-SHA1, 6 digits, 30-second steps) with recovery codes. It
needs `CREDENTIALS_KEYS`, which seals the secret in the database (AES-256-GCM, see `NOT_CONFIGURED`).

| Route | Auth | Request | Response |
|---|---|---|---|
| `GET /api/v1/auth/totp` | session | | `{"enabled", "enabled_at", "recovery_codes_remaining"}` |
| `POST /api/v1/auth/totp/setup` | session + CSRF | | `{"secret", "otpauth_url"}`; replaces a pending secret; `409 TOTP_ALREADY_ENABLED`; `409 NOT_CONFIGURED` without `CREDENTIALS_KEYS`; `403 FORBIDDEN` for the demo account |
| `POST /api/v1/auth/totp/enable` | session + CSRF | `{"code"}` | `{"recovery_codes": [10 strings]}`; `422 INVALID_TOTP_CODE`; `409 TOTP_ALREADY_ENABLED`; `409 EMAIL_UNVERIFIED`; `403 FORBIDDEN` for the demo account |
| `POST /api/v1/auth/totp/disable` | session + CSRF | `{"code"}` (TOTP or recovery) | `204`; `422 INVALID_TOTP_CODE`; `409 TOTP_NOT_ENABLED`; `429` |
| `POST /api/v1/auth/totp/verify` | none (Origin check) | `{"challenge", "code"}` | `200` `LoginOut` with cookies; `401 INVALID_TOTP_CODE`; `401 TOTP_CHALLENGE_INVALID`; `429` |

- **Login now returns `LoginOut`**, a union discriminated by `status`: `{"status": "signed_in", "user": User}`
  with the session cookies set, as before but wrapped, or `{"status": "totp_required", "challenge",
  "expires_at"}` with **no cookies** when the password was right and the account has two-factor
  authentication. A wrong password still answers `401 INVALID_CREDENTIALS` before any of this, so the
  challenge only ever means "the first step passed".
- **The challenge** is signed with `SECRET_KEY` (HMAC-SHA256, its own signing context) over the user id, an
  expiry, a random nonce and a fingerprint of the user's password, and lasts 5 minutes. It is not stored. It
  signs nobody in by itself; it names whom a code is for. Expired, forged, malformed, for a user that does
  not exist, for a user whose two-factor authentication has been turned off since, and issued before the
  user's password changed: all `401 TOTP_CHALLENGE_INVALID`.
- **Ruling: a challenge does not outlive a password change.** The fingerprint is the first 16 bytes
  (hex) of SHA-256 over the stored password hash, or a fixed value for an account with no password. `verify`
  recomputes it from the user row it has locked, so any change of the password (a reset by email, the
  admin CLI, setting a first password on a provider-only account) ends every open challenge of that user,
  and the person signs in again with the new password. A password is often replaced because it leaked, and
  a sign-in that passed the first step with the old one must not become a session afterwards. The token is
  signed, not encrypted, and carries only the truncated digest, never the hash. The refused attempt is
  not counted as a failed sign-in.
- **Ruling: a sign-in sees two-factor authentication being turned on while it runs.** The password check is
  slow, so a person can finish enabling two-factor authentication in another browser while a password
  sign-in (or a GitHub/Google callback) for the same account is in flight. Both read the user row under a
  shared lock after the first step and decide from that read whether to issue a challenge or a session, so
  the late sign-in gets `totp_required` (or the redirect to the second step) and never a password-only
  session.
- **Codes.** A code is accepted for the current 30-second step and one step either side. It is accepted
  once: the step of the last accepted code is stored, and a code for an earlier or equal step is refused,
  so a code that was seen cannot be replayed. The code that enables two-factor authentication counts as
  used, so signing in right after enabling needs the next code. Spaces and hyphens in a typed code are
  ignored.
- **Recovery codes.** `enable` returns 10 codes of 10 characters from `a-z2-7`, shown as `abcde-fghij`,
  once and never again. Only their SHA-256 is stored. A recovery code is accepted wherever a code is
  (`verify`, `disable`), case-insensitively and without the hyphen, and works once. `GET /auth/totp`
  reports how many are left. `disable` deletes all of them, and `enable` makes a fresh set.
- **Two checks on one account never both succeed with one code.** `verify`, `enable` and `disable` read the
  user row `FOR UPDATE`, so two requests presenting the same code run one after the other and the second is
  refused.
- **Throttling.** Wrong codes at `verify` are recorded as failed sign-ins for the account's email and the
  client IP, so they share the password's limit: 5 failures per email (20 per IP) in 15 minutes, and the next
  attempt is `429` with `Retry-After`, checked before the code is looked at. The user row is locked before
  the failures are counted, so concurrent attempts cannot overshoot the limit. A throttled request records
  nothing. `disable` needs a code too, and a stolen session must not be able to try them all, so it allows
  **5 attempts per user per 15 minutes** (`429 RATE_LIMITED`); this limit is independent of the sign-in one.
- **Ruling: `enable` needs a verified email when email is configured** (`409 EMAIL_UNVERIFIED`, checked
  before the code), for the reason given under linking providers: anyone can register an address that is
  not theirs, and a second factor set on that account would outlast the real owner's recovery. Without
  email configured nobody can verify an address, so the rule does not apply.
- **Ruling: `enable` without a `setup` is `422 INVALID_TOTP_CODE`**, with a `detail` that says to start the
  setup first; there is no secret for any code to be right for. `setup` never reveals an existing secret:
  it answers `409 TOTP_ALREADY_ENABLED` once enabled, and replaces the secret before that.
- **Ruling: the demo account cannot set up or enable two-factor authentication** (`403 FORBIDDEN`): it is a
  single shared, anonymous account, and one visitor turning it on would lock every later visitor out.
- **Secrets are returned once and never logged.** The `setup` response carries `secret`, the `enable`
  response carries the recovery codes, and both are sent with `Cache-Control: no-store`. The secret is
  stored sealed, with the id of the key it was sealed under. If the secret can no longer be opened (the key
  left `CREDENTIALS_KEYS`), signing in fails closed instead of skipping the second factor; recovery codes
  still work.
- **Operator recovery.** Someone who lost both the authenticator app and the recovery codes cannot turn
  two-factor authentication off themselves, since that needs a code. `spanlight reset-2fa --email <address>`
  does it from the host: it clears the secret, the stored step and every recovery code in one transaction
  and writes a `user.totp_disable` audit event to each org of the user with no actor and `metadata`
  `{"via": "cli"}`, then prints one line. An unknown address exits non-zero and changes nothing; a user
  without two-factor authentication exits zero, says so and writes nothing. The password and the sessions
  are not touched (`spanlight reset-password` revokes sessions). The user then signs in with the password
  alone and can enrol again; in an org that requires two-factor, they must.
- **`GET /api/v1/auth/me` gains a top-level `totp_enabled` boolean**, next to `has_password`. It is not on
  `User`, which other people's profiles are also shown through.
- **Audit**: `user.totp_enable` and `user.totp_disable` are written to each org the user belongs to, with
  `actor` and `target` the user and no `metadata`. A failed attempt writes nothing.

**Requiring it for an organization**

- `PATCH /api/v1/orgs/{org_id}` with `{"require_2fa": true|false}` needs the owner-only permission
  `org:security` (admins get `403 FORBIDDEN`, non-members `404`). `require_2fa` must be a boolean: `null`
  or anything else is `422 VALIDATION_ERROR`. Setting it to its current value is a no-op: `200`, no audit
  event. The same route also renames the organization; see Organization and project administration.
- Turning it **on** needs `CREDENTIALS_KEYS` (else `409 NOT_CONFIGURED`) and two-factor authentication on the
  owner's own account (else `409 TWO_FACTOR_NOT_ENABLED`), so the owner can always meet the rule they set.
  Turning it off needs neither. Each change writes an `org.update` audit event whose `metadata` has
  `"require_2fa": {"from": <bool>, "to": <bool>}`.
- While it is on, a member of that org without two-factor authentication gets **`403 TWO_FACTOR_REQUIRED`**
  on every route scoped to the org or one of its projects. Membership is checked first, so a non-member
  still gets `404`; the check comes before the role check. `/api/v1/auth/*` is not affected, which is how
  the member turns two-factor on, and `GET /auth/me` still lists the org. Ingestion with an API key is not
  affected: a key is not a person signing in.
- A member who turns their own two-factor authentication off while an org of theirs requires it is locked
  out of that org until they enable it again. Nothing prevents it.

## API key scopes, expiry and bearer requests (added 2026-10-08)

A project API key (`spl_live_…`) used to do one thing, ingest. It now carries scopes and may expire, and a
key can read its own project through the dashboard API.

| Scope | Allows |
|---|---|
| `ingest:write` | `POST /v1/traces` and `POST /v1/otlp/traces` |
| `traces:read` | the read routes listed below, for the key's own project |
| `scores:write` | `POST /v1/scores` (added 2026-10-11) |
| `prompts:read` | `GET /v1/prompts/{name}` (added 2026-10-11) |

- **Creating a key.** `POST /api/v1/projects/{project_id}/keys` takes `{"name", "scopes"?, "expires_at"?}`.
  `scopes` is a non-empty list of the values above and defaults to `["ingest:write"]`; repeated values are
  stored once, in the order first given. An unknown scope, an empty list, or an `expires_at` that is not in the
  future is `422 VALIDATION_ERROR` (`errors[].field` is `scopes…` or `expires_at`). `expires_at` is an ISO-8601
  time; one without an offset is read as UTC, and `null` (the default) means the key never expires. The
  database enforces the scope list as well (`api_keys_scopes_check`).
- **Shapes.** `ApiKey` gains `scopes` (list of strings) and `expires_at` (time or `null`), in the list and in
  the 201 response. The `key.create` audit event's `metadata` gains `scopes` and `expires_at`. Keys created
  before this change have `["ingest:write"]` and no expiry, so none of them changes behaviour.
- **Ingestion.** A key without `ingest:write` gets `403 KEY_SCOPE` on both ingestion routes.
- **Expiry.** A key at or past its `expires_at` is `401 KEY_EXPIRED`, on ingestion and on reads. This is only
  said to a caller who proved they hold the key: a wrong secret is `401 UNAUTHORIZED` whatever the key's state,
  and a revoked key is `401 UNAUTHORIZED` even if it has also expired.
- **Reading with a key.** A key with `traces:read` may call these routes, and only for its own project:
  `GET /api/v1/projects/{project_id}/traces`, `…/traces/{trace_id}`, `…/sessions`, `…/filters`,
  `…/metrics/overview`, `…/metrics/timeseries`, `…/metrics/models`, `…/releases`, `…/releases/compare`, `…/users` and
  `…/users/{external_user_id}`. Responses are the same as for a session.
  - The project in the path must be the key's. Any other project, existing or not, is `404 NOT_FOUND`, the same
    answer a non-member gets, and the check comes before the scope check. Row-level security is bound to the
    key's project for the request, as it is for a member.
  - For its own project, a key without `traces:read` is `403 KEY_SCOPE`.
  - Every other `/api/v1` route answers an API key with `403 KEY_SCOPE`. That includes the routes that only a
    session may use (`/auth/*`, creating an organization, accepting an invite) and the routes that need no
    sign-in at all (signup, login, email verification, password reset, `POST /auth/totp/verify`,
    `POST /demo/session` and the `/auth/oauth/…` routes): a key is refused there rather than served as an
    anonymous request, and nothing is created or signed in. A key never has an organization role, so it
    cannot manage keys, members or projects.
  - A bearer that is not a valid, unrevoked, unexpired key is `401` on every `/api/v1` route (`UNAUTHORIZED`,
    or `KEY_EXPIRED` for an expired key), before any of the above.
  - A key is not a user, so reads with it are not subject to `TWO_FACTOR_REQUIRED`, which is a check on a
    person's account. Creating the key is: it needs a session, and an organization that requires two-factor
    authentication refuses a member without it (see Two-factor authentication).
  - Reads update `last_used_at` like ingestion (at most once a minute), including requests that are then
    refused with 403 or 404, since the key did authenticate.
- **Bearer requests ignore cookies.** When a request to `/api` has an `Authorization: Bearer …` header, its
  `Cookie` header is dropped before routing and the CSRF and `Origin` checks are skipped: a bearer credential
  is not attached by the browser on another site's behalf. The header is never combined with, or replaced by,
  a cookie. A bearer that is empty or carries a key that is malformed or unknown is `401 UNAUTHORIZED` even
  when the request also has a valid session cookie. The scheme name is matched case-insensitively. A page on
  another site cannot add the header to a request without a CORS preflight, and the API sends no CORS headers.
- **Only `Bearer` counts.** Any other `Authorization` scheme (Basic, Negotiate, Digest, or an empty value) is
  ignored: the request keeps its cookies and is checked as if the header were absent, including the `Origin`
  check and CSRF. Browsers attach Basic and Negotiate credentials by themselves when a proxy in front of the
  app asks for them, so treating those as a bearer would let a cross-site request skip the `Origin` check
  (a login CSRF). A signed-in dashboard request that happens to carry such a header keeps working through its
  cookie.

## Personal access tokens (added 2026-10-08)

A personal access token (PAT, `spl_pat_<12 characters>_<32 characters>`, lowercase base32) is a credential a
person creates for scripts and tools. It acts as that person on the dashboard API, with their organization
roles, and never as more than its own scope allows. It is sent as `Authorization: Bearer spl_pat_…`. Only a
signed-in browser session manages tokens.

| Route | Auth | Request | Response |
|---|---|---|---|
| `GET /api/v1/auth/tokens` | session | none | `[{id, name, prefix, scope, created_at, expires_at, last_used_at}]`, active tokens, newest first |
| `POST /api/v1/auth/tokens` | session | `{"name", "scope", "expires_at"?}` | `201`, the same object plus `"token"` |
| `DELETE /api/v1/auth/tokens/{token_id}` | session | none | `204` |

- **Creating.** `name` is 1 to 100 characters. `scope` is `read` or `write` and has no default: leaving it out,
  or sending another value, is `422 VALIDATION_ERROR`. `expires_at` follows the rules for API keys: an ISO-8601
  time in the future (one without an offset is UTC), or `null` (the default) for a token that never expires.
  The 201 carries `Cache-Control: no-store`.
- **The secret is shown once.** `token` is in the 201 response and nowhere else. The list, `GET /auth/me` and
  every later response omit it, and the database keeps only the SHA-256 of the secret. The `prefix`
  (`spl_pat_` and 12 characters) is what the list shows to tell tokens apart. A token cannot be recovered; create
  another.
- **Listing** returns the caller's tokens that can still be used: revoked and expired tokens are not listed.
- **Revoking** sets `revoked_at` and is idempotent: revoking a revoked token is another `204` and keeps the first
  time. A token that does not exist, or belongs to someone else, is `404 NOT_FOUND`, and revoking never reveals
  which. The revoked token is `401 UNAUTHORIZED` from its next use.
- **The demo account cannot create tokens** (`403 FORBIDDEN`). Every demo visitor is the same user, so a token
  would outlive the visit and be usable by anyone who saw it.
- **Creating and revoking are logged**, not audited: the structured logs `pat_created` and `pat_revoked` carry
  the user id, token id and scope (and the expiry, for creation), and never the token or its secret. A token
  belongs to a user and not to an organization, so there is no organization audit event for it.

**What a token may do**

- **Memberships are read on every request.** A token has no permissions of its own. The organization or
  project in the path is looked up against the owner's memberships on each call, so removing the member, or
  changing their role, takes effect on the next request. A project or organization the owner does not belong to
  is `404 NOT_FOUND`, the answer a session gets, and that is decided before the scope or the role.
- **Scope.** Every permission is classed `read` or `write`. `org:read`, `project:read` and `audit:read` are
  `read`; every other permission is `write`. A `write` token may do whatever its owner's role allows. A `read`
  token is refused with `403 TOKEN_SCOPE` on a `write` permission, and also on any method other than GET, HEAD
  and OPTIONS: leaving an organization (`DELETE /orgs/{org_id}/members/{own id}`) needs only `org:read`, and a
  read-only token must not be able to do it. Note that listing invites needs `member:manage`, which is a
  `write` permission, so a `read` token cannot list invites. The checks run in this order: a non-member is
  `404`, then `403 TWO_FACTOR_REQUIRED`, then `403 FORBIDDEN` for a role without the permission, then
  `403 TOKEN_SCOPE`. A token is therefore never told anything a session of the same user would not be.
- **Two-factor authentication applies.** In an organization that requires it, a token whose owner has not
  turned it on gets `403 TWO_FACTOR_REQUIRED` on the organization's routes, like their session. `GET /auth/me`
  still answers, so a tool can tell whose token it is.
- **Bearer requests ignore cookies, `Origin` and CSRF**, as for API keys: the `Cookie` header is dropped and a
  token is never combined with a session. A request that carries a token and the cookies of another user is
  served as the token's user. A bearer that is malformed, unknown, or revoked is `401 UNAUTHORIZED`, even when
  a valid session cookie is also sent.
- **Expiry.** A token at or past its `expires_at` is `401 TOKEN_EXPIRED`, on every `/api/v1` route. As for API
  keys this is only said to a caller who proved they hold the token: a wrong secret is `401 UNAUTHORIZED`
  whatever the token's state, and a revoked token is `401 UNAUTHORIZED` even if it has also expired.
- **`last_used_at`** is written at most once a minute, including for requests that are then refused with 403 or
  404, since the token did authenticate.

**Where a token is accepted**

- Every `/api/v1` route that names an organization or project (guarded by a permission), under the rules above.
  These include the read routes an API key with `traces:read` may use, which a token reaches in every project
  its owner belongs to and not only one.
- `GET /api/v1/auth/me`, which answers with the owner's account and memberships. It does not set cookies for a
  token.
- Nowhere else. Every route that needs a signed-in session answers a token with `403 SESSION_REQUIRED`: all of
  `/api/v1/auth/*` except `GET /auth/me` (so a token cannot create, list or revoke tokens, change two-factor
  authentication, or manage sessions or linked accounts), `POST /orgs`, `GET /invites/preview` and
  `POST /invites/accept`. So do the routes that need no sign-in (signup, login, email verification, password
  reset, `POST /auth/totp/verify`, `POST /demo/session`, `/auth/oauth/…`): a token is refused there rather than
  served as an anonymous request. An API key gets `403 KEY_SCOPE` on all of these, as before.
- A token is not an API key: on `POST /v1/traces` and `POST /v1/otlp/traces` it is `401 UNAUTHORIZED`, and an
  API key on the token routes is `403 KEY_SCOPE`.

Bearer reads of `/api/v1` (`GET` and `HEAD`, with a token or an API key) are rate limited per credential: 20
requests a second with bursts of 40, counted once the credential is known to be valid. Over the limit the answer
is `429 RATE_LIMITED` with `Retry-After`. Bearer writes and signed-in browsers are not limited per credential.

## Organization and project administration (added 2026-10-09)

| Route | Permission | Request | Response |
|---|---|---|---|
| `PATCH /api/v1/orgs/{org_id}` | `org:update` (admin, owner); `org:security` (owner) for `require_2fa` | `{"name"?, "require_2fa"?}` | `200` `Org`; audit `org.update` |
| `DELETE /api/v1/orgs/{org_id}` | `org:delete` (owner) | `{"confirm": "<org slug>"}` | `204` |
| `DELETE /api/v1/projects/{project_id}` | `project:delete` (admin, owner) | `{"confirm": "<project slug>"}` | `204`; audit `project.delete` |

**Renaming**

- `PATCH /orgs/{org_id}` takes `name`, `require_2fa`, or both. At least one is required: an empty body is
  `422 VALIDATION_ERROR`. A field that is left out is left alone, and `null` is not a way to leave it out: it
  is a `422` too (the OpenAPI document lists the fields as nullable, but the route refuses `null`). Unknown
  fields are `422`. `name` follows the rules for creating an organization: trimmed, 1 to 100 characters.
- A rename changes the name only. The `slug` stays as it was, because it is what a deletion asks to be typed
  out and what links and the audit log refer to.
- Each field has its own permission and a request with both needs both: an admin who sends `require_2fa`,
  alone or with a `name`, gets `403 FORBIDDEN` and nothing is applied. If turning `require_2fa` on is
  refused (`409 NOT_CONFIGURED`, `409 TWO_FACTOR_NOT_ENABLED`), a `name` sent with it is not applied either.
- Only values that change are applied. Sending the current values is a no-op: `200`, no audit event. A change
  writes one `org.update` event whose `metadata` lists just the changed fields, each as
  `{"from": …, "to": …}`: `{"name": {"from": "Acme", "to": "Acme Labs"}}`, or both `name` and `require_2fa`.

**Deleting**

- The caller types the slug of the organization (or project) being deleted as `confirm`. It is compared
  exactly as typed: not trimmed, and case-sensitive, so the display name does not match. A wrong value is
  `422 CONFIRMATION_MISMATCH` and nothing is deleted. A body without `confirm`, or with something that is
  not a string of at most 200 characters, is `422 VALIDATION_ERROR`. Permission is checked first, so a caller
  who may not delete gets `403 FORBIDDEN` (a non-member `404`) whatever they typed.
- **Organization.** Its projects are deleted first, with their traces, spans and API keys, and then the
  organization, with its memberships, invites and audit events, all in one transaction: either all of it is
  deleted or none is. It is synchronous, which is fine for the organizations this serves. People are not
  deleted: they simply no longer belong to it, so `GET /auth/me` stops listing it. Its API keys stop working
  (`401`). An organization has no audit log left to write to, so the deletion is logged instead: the structured
  log `org_deleted` carries `org_id`, `project_ids` and `actor_user_id`, and nothing else.
- **Project.** Its traces, spans and API keys are deleted with it. The organization's audit log keeps a
  `project.delete` event, written in the same transaction, with `target_type` `project`, `target_id` the
  project's id and `metadata` `{"name": "<name>", "slug": "<slug>"}`, since the project can no longer be looked
  up. Its slug can be used again by a new project.
- **The demo organization** cannot be renamed, have its settings changed, or be deleted, and neither can its
  projects: `403 FORBIDDEN`.
- A personal access token needs the `write` scope for all three routes (all of them are changes), like any
  other non-GET request. An API key is `403 KEY_SCOPE`.

## Idempotency keys (added 2026-10-09)

A `POST` route that accepts an `Idempotency-Key` header can be retried safely: it runs once, and a retry is
answered with the first answer. A route that accepts the header says so and lists it among its parameters in
the OpenAPI document; on any other route the header is ignored.

- **The header** is optional. A key is 1 to 128 visible ASCII characters (no spaces, control characters or
  non-ASCII); anything else is `422 VALIDATION_ERROR` with `errors[].field` `Idempotency-Key`, and the route
  does not run. A request without the header behaves as it always did and nothing is stored.
- **Whose key it is.** A key belongs to the caller that sent it: to the user for a browser session or a
  personal access token, so a script and the dashboard acting as the same user share their keys, and to the
  API key for an API key. Different callers can use the same key without affecting each other.
- **Which request it was.** Two requests are the same when their method, path and body match. A JSON body is
  compared as JSON, so key order and whitespace do not matter. The query string is not part of the
  comparison, so routes that accept the header take their parameters in the body.
- **First request.** The route runs. Its status, JSON body and `Content-Type` are kept for 24 hours.
- **Same key, same request, after the first has answered.** The kept status and body are returned with the
  original `Content-Type` and `Idempotent-Replayed: true`, and the route does not run again. Other response
  headers are not replayed: the replay has the `X-Request-ID` of the retry.
- **Same key, different request.** `422 IDEMPOTENCY_MISMATCH`, whether the first request has answered or not.
  Send a new key for a different request.
- **Same key while the first request is still running.** `409 IDEMPOTENCY_IN_PROGRESS`; retry in a moment. A
  first request that has not answered after 60 seconds is taken to have died: the next request with the key
  runs the route and takes the key over, so a request slower than a minute can run twice.
- **What is kept.** Every `2xx` and `4xx` answer, including a `422` for a body the route rejects, so a client
  that fixes a rejected request must send it under a new key. A `5xx` is never kept: the key is released so
  that the retry runs. Authentication failures (`401`, `403 CSRF_FAILED`, `403 ORIGIN_NOT_ALLOWED`) and refusals
  by the route's permission check (`403`, `404`) come before the key is looked at, so they are never kept.
- **A replay is authorized like the request it replays.** The permission check runs again before the key is
  looked at, so a caller who has lost access since, such as a member who was removed or a token that was
  revoked, gets the `401`, `403` or `404` the original request would now get, not the kept answer.
- **JSON only, up to 1 MiB.** The kept body is JSON. If a route answers with anything else (another content
  type, a file), or with a JSON body of more than 1 MiB, the answer is delivered as it is but cannot be kept,
  and the key is released.
- **The limit of the guarantee.** If the server stops after the route has committed its changes and before
  the answer is kept, the key stays unanswered for a minute and the retry then runs the route again.
- **Retention.** A key is forgotten 24 hours after the first request reserved it. A key past that time is
  ignored even before the cleanup job removes it, and can be used again for any request.

## Exports (added 2026-10-09)

- **Trace exports** are asynchronous. `POST /api/v1/projects/{id}/exports` answers `202` with the export in
  status `queued`; the `create_export` job writes the file to object storage and moves it to `running`, then to
  `done` or `failed`. Poll `GET .../exports/{export_id}` (or the list) for the status. It needs object storage:
  without `S3_BUCKET`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` the request is `409 NOT_CONFIGURED`.
- **Who may export.** `export:create` is held by members, admins and owners; a viewer is `403`. A session or a
  personal access token with the `write` scope can create one; an API key cannot (`403 KEY_SCOPE`). The routes
  accept an `Idempotency-Key`: a retry with the same key and body returns the same export.
  Like any answer to a keyed request, a `409 NOT_CONFIGURED` is kept for 24 hours and replayed for the same key and
  body, so after turning object storage on, retry with a new key.
- **Filters.** `filters` takes the trace-list filters under the same names (`from`, `to`, `environment`, `release`,
  `model`, `status`, `error_class`, `user_id`, `session_id`, `tag`, `q`) with the same meaning (`error_class` is one of
  the nine classes and keeps traces with at least one span of that class). `from` and `to` are required and
  `to - from` is at most 90 days; a time without an offset is UTC. An unknown filter is `422`.
- **Size limit.** More than 100 000 matching traces end the export as `failed` with `error_code`
  `EXPORT_TOO_LARGE`, and the job does not retry. The `error_code` values are `EXPORT_TOO_LARGE`, `NOT_CONFIGURED`
  (storage was switched off after the request), `EXPORT_TIMEOUT` (a read ran past two minutes; not retried) and
  `EXPORT_FAILED` (every attempt failed, or the job was lost and cleanup gave up on it after 24 hours).
- **Download links.** `download_url` is present only while the status is `done`, in the list as well as on a single
  export, and is a presigned link valid for one hour. Fetch the export again (`GET .../exports/{export_id}`) for a
  fresh link; none is given once `expires_at` has passed. A file is deleted 7 days after the export completed and
  the export becomes `expired`, with no link. Deleting a project deletes its export files too.
- **File contents.** CSV has one row per trace with the columns `trace_id, name, started_at, ended_at, duration_ms,
  status, environment, release, user_id, session_id, span_count, error_count, input_tokens, output_tokens,
  cost_usd, tags`, CRLF line ends and UTF-8. `status` is `error` when the trace has a failed span, else `ok`. An
  unknown value is an empty cell, `cost_usd` is a decimal string, and tags are joined with `;`. A cell that starts
  with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'` so that a spreadsheet does not run it as
  a formula. JSONL has one JSON object per line: the fields of the trace detail endpoint plus `status`, with the
  spans nested under `spans`. Both are ordered newest trace first.
- **Audit log filters.** `GET /api/v1/orgs/{id}/audit` accepts `action` (exact match), `actor_id`, `from`
  (inclusive) and `to` (exclusive); all are optional. `from` must be earlier than `to`.
- **Audit log CSV.** `GET /api/v1/orgs/{id}/audit/export.csv` takes the same filters and streams the matching
  events as CSV (`id, created_at, action, actor_id, actor_email, target_type, target_id, ip, metadata`, with
  `metadata` as JSON), newest first, under the same formula protection. More than 50 000 matching events are
  `422 EXPORT_TOO_LARGE`, answered before anything is sent; narrow the filters. Like the other audit routes it
  needs `audit:read`.

## Provider credentials (added 2026-10-09)

- **Routes.** `GET /api/v1/orgs/{id}/credentials` needs `org:read` (any member). Adding (`POST`), rotating
  (`POST .../{credential_id}/rotate`), checking (`POST .../{credential_id}/check`) and deleting (`DELETE
  .../{credential_id}`) need `credentials:manage`, which only owners hold; an admin is `403`, a non-member `404`.
  The check calls the provider with the organization's key, so it is an owner action too.
- **The key is write-only.** `api_key` is accepted in the create and rotate bodies, trimmed of surrounding
  whitespace, and never returned. Responses that describe a credential carry `Cache-Control: no-store`. The routes
  take no `Idempotency-Key`, so that no copy of a request body with a key in it is kept anywhere.
- **Base URL.** `base_url` is required for `openai_compatible` and refused for `openai` and `anthropic` (`422`,
  field `base_url`). It must be `https://`, without a user name, password, query or fragment, and its host must
  resolve to public addresses only; trailing slashes are removed. Any failure is `422 VALIDATION_ERROR` with
  `errors[].field` `base_url`, including a host that does not resolve. With `GATEWAY_ALLOW_INSECURE_BASE_URLS=true`
  `http://` and private addresses are accepted. Rotating re-checks the stored base URL against the current policy.
- **Errors.** `409 NOT_CONFIGURED` when `CREDENTIALS_KEYS` is not set (create, rotate and check); `409
  CREDENTIAL_NAME_TAKEN` for a name already used in the organization (names are compared exactly, after trimming);
  `409 CREDENTIAL_IN_USE` when deleting a credential that a gateway route targets.
- **Check.** `POST .../check` lists the provider's models with the key (`GET /v1/models`) without following
  redirects, with a 10 second timeout. It answers `200` either way: `{"status": "ok", "checked_at", "error": null}`
  or `{"status": "error", "checked_at", "error": "401 Unauthorized"}`. `error` is a short status line (the HTTP
  status and its standard phrase, `timeout`, `connection error`, `host not found`, `blocked address` or `key
  cannot be decrypted`), never the provider's response body. The outcome is stored as `last_checked_at` and
  `last_error`; both are `null` for a credential never checked. Rotating clears them, since they described the old
  key. `last_used_at` is `null` until the gateway uses the credential, then moves at most once a minute, like a
  key's. A call uses the credential of every attempt it sent upstream; an attempt refused by the egress rules sent
  nothing and does not count.
- **In use.** Only a route's current config counts when deleting a credential. An older version may still name a
  deleted credential; reverting to it is then `422` on `config.targets.<i>.credential_id`.

## Gateway routes (added 2026-10-09)

- **Routes.** Under `/api/v1/projects/{id}/gateway/routes`: `GET` (list, by name, unpaginated), `GET /{route_id}`
  and `GET /{route_id}/versions` (newest first, unpaginated) need `project:read`; `POST`, `PUT /{route_id}`, `POST
  /{route_id}/revert`, `POST /{route_id}/default` and `DELETE /{route_id}` need `gateway:write` (owners and
  admins).
- **Config.** Unknown fields anywhere in `config` are `422`. Counts and times are strict integers: `true` or `2.0`
  is refused. `retry` and `fallback` are required objects, but every field inside them has a default, so `{}`
  takes the defaults. `max_backoff_ms` must be at least `backoff_ms` even when it is left at its default, so
  `{"backoff_ms": 5000}` alone is `422`. Every field path is relative to the request body: schema errors and the
  organization check on credentials alike (`config.targets.<i>.credential_id`; the spec writes it relative to
  the config, `targets.<i>.credential_id`). A revert, whose body has no config, reports the same `config.` paths
  for the saved config it would restore.
- **Reading stored configs.** Responses return a stored config as it was saved, without applying today's bounds
  again, so a rule tightened later never makes a route unreadable; saving it again (a revert) meets the new rules.
- **Versions.** Creating a route stores version 1; each `PUT` and each revert stores the next version, so the
  history is never rewritten. `PUT` carries `expected_version`; when the route has moved on it is `409
  ROUTE_VERSION_CONFLICT`, and the problem body carries the current version as an extension member,
  `current_version`, as well as naming it in `detail`. A revert to a version the route never had is
  `404`; a revert whose old config no longer validates (a credential deleted since, or a tightened rule) is `422`.
- **Default.** The project's first route becomes its default. `POST .../default` moves the default and is not a
  new version: `version`, `updated_by` and `updated_at` describe the last config save. It is audited as
  `gateway_route.update` with `{"is_default": true}`. Deleting the default route leaves the project with no
  default until another route is made default.
- **Errors.** `409 ROUTE_NAME_TAKEN` for a name already used in the project (compared exactly, after trimming);
  `409 ROUTE_IN_USE` when deleting a route a gateway key uses.
- **Revoked keys.** Only an active gateway key makes a route in use. Deleting a route clears it from the revoked
  keys that still name it, so their `route_id` reads `null` from then on.

## Gateway keys (added 2026-10-09)

- **Routes.** Under `/api/v1/projects/{id}/gateway/keys`: `GET` (list, newest first, revoked keys included,
  unpaginated) needs `project:read`; `POST`, `PATCH /{key_id}` and `DELETE /{key_id}` need `gateway:write` (owners
  and admins).
- **The key is shown once.** The `POST` response is the only one with `secret`, the full `spl_gw_…` key, and it
  carries `Cache-Control: no-store`. Every other response shows `prefix` (`spl_gw_` and the key's 12 character id),
  which is not secret.
- **Fields.** `name` 1–100 characters and `environment` 1–64, both trimmed; `rpm_limit` 1–100 000 and `tpm_limit`
  1–100 000 000, `null` for no limit; `cache_ttl_seconds` 1–86 400, `null` for no cache; `allowed_models` at most
  100 model names, empty for any model; `default_tags` at most 20 tags of 1–64 characters, the bounds of
  `x-spanlight-tags`. Numbers are strict integers; repeated models or tags are `422`. `fault_profile_id` is the
  fault profile the key runs (see Gateway Lab below), `null` for none.
- **Bounds differ from the plan.** `allowed_models` holds at most 100 names and `default_tags` at most 20 tags;
  the plan said 64 and 10. The backend's values stand: 20 tags is also the bound of `x-spanlight-tags`, which
  default tags are merged into.
- **Route.** Without `route_id` a new key uses the project's default route, and the request is `422
  NO_DEFAULT_ROUTE` when the project has none. A `route_id` that is not one of the project's routes is `422
  VALIDATION_ERROR` on `route_id`. The key keeps its route when the default moves later.
- **Edits.** `PATCH` changes only the fields sent. `rpm_limit`, `tpm_limit` and `cache_ttl_seconds` are cleared by
  sending `null`, and so is `fault_profile_id` (detach); `null` for any other field is `422`. An edit that changes
  nothing is not audited. A revoked key cannot be edited (`404`).
- **Revoking.** `DELETE` revokes the key (`204`); revoking a revoked key is a `204` that changes nothing. The key
  stays listed with `revoked_at` set, so the spans sent with it keep their attribution.
- **On `/api/v1`.** A gateway key authenticates the gateway only. Sent as a bearer to an `/api/v1` route it is an
  unknown credential: `401 UNAUTHORIZED`, like any credential that is not a project API key or an access token.
- **Span attribution.** Spans written by the gateway record the key in `spans.source_key_id`. Native and OTLP
  ingestion leave it `null`: a project API key is not a gateway key. A span the gateway recorded belongs to the
  gateway: native or OTLP ingestion of a span with the same trace and span id is accepted (counted in `accepted`,
  so a replay stays idempotent) but never overwrites it, so its cost, tokens and key cannot be rewritten from an
  SDK.

## Gateway cache (added 2026-10-09)

- **Cache key covers the route.** The key is the SHA-256 of the surface, the route id, the route's saved version and the canonical body, not the surface and body alone as the design listed. Routes carry the model aliases and credentials that decide which model answers, so two routes never share an answer, and saving or reverting a route (a new version) leaves its earlier entries unreachable until they expire. Entries stay scoped by project as well.
- **Answers without usage are not stored.** A `200` whose body reports no token usage (some OpenAI-compatible servers omit it) is never cached: a hit must report the original usage of the answer it replays. Such calls always reach the provider.
- **Purge.** `POST /api/v1/projects/{id}/gateway/cache/purge` (`gateway:write`, owners and admins) answers `204` and deletes every cache entry of the project, expired or not. Purging an empty cache succeeds. The audit event `gateway_cache.purge` targets the project and records `{"deleted": n}`.

## Gateway Lab: fault profiles (added 2026-10-09)

- **Routes.** Under `/api/v1/projects/{id}/gateway/fault-profiles`: `GET` (by name, unpaginated) needs
  `project:read`; `POST`, `PATCH /{profile_id}` and `DELETE /{profile_id}` need `gateway:write`. Audit events
  `fault_profile.create|update|delete` target the profile.
- **Fields.** `name` 1–100 characters, trimmed, unique per project (`409 FAULT_PROFILE_NAME_TAKEN`); `scenario` one of
  the nine of the design; `probability` a JSON number from 0 to 1 with at most three decimals (default 1, a float in
  the response); `enabled` (default `true`); `expires_at` optional, timezone-aware, `422` on `expires_at` when it is
  not in the future (an unchanged `expires_at` echoed back in a `PATCH` is not re-checked); `null` removes it.
- **Params.** Validated per scenario with the design's bounds; omitted `params` are the scenario's defaults, and the
  response always shows every param. A param that does not belong to the scenario, or a value out of bounds, is `422`
  on `params.<name>`. In a `PATCH`, `params` must be sent with `scenario` (`422` on `params` otherwise), and a
  different `scenario` sent without `params` resets them to that scenario's defaults.
- **Output.** `active` is `enabled` and not expired at the time of the request; `attached_key_ids` lists the active
  (not revoked) keys that run the profile, newest first.
- **Production guard.** Attaching a profile (`PATCH` of a key with `fault_profile_id`) to a key whose `environment`
  is `production`, or setting `environment` to `production` on a key that has a profile, is
  `422 FAULT_PROFILE_ON_PRODUCTION_KEY`. A `fault_profile_id` that is not a profile of the project is `422` on
  `fault_profile_id`. The check runs under the key's row lock.
- **Production means any casing.** The guard compares `environment` trimmed and case-insensitively, so
  `Production`, ` PRODUCTION ` and `production` all count as production, at attach time and again when the gateway
  decides a fault.
- **Reads are lenient.** Stored params are loaded without validation, so a bound tightened later never fails a `GET`;
  a `PATCH` or `POST` meets the current bounds.
- **Delete.** Deleting a profile is `204`; the keys that ran it are detached by the database (`ON DELETE SET NULL`
  on the profile column), and the audit event lists their ids.

## Gateway overview (added 2026-10-09)

- **Route.** `GET /api/v1/projects/{id}/gateway/overview?from&to&environment` (`project:read`). The window is at most 7 days, else `422 VALIDATION_ERROR` on field `from`. It is always read from the raw spans of gateway keys (spans with `source_key_id`), never the hourly rollups.
- **Cache hit rate.** `cache.hit_rate` is `hits / (hits + misses)`, not hits over all requests as the plan's example had it. Calls of keys with the cache off and streams never look in the cache, so they are not in the denominator; when `hits + misses` is zero the rate is `null` (the UI shows "—"), so a project without caching does not read as 0 %.
- **Errors include Lab faults.** `errors` and `error_rate` count calls failed by a Lab fault; `faults` lists the per-scenario counts so a client can show how many.
- **Fallbacks and retries are event counts.** `fallbacks` sums the switches to another target and `retries` the repeated tries on one target, over all requests. A request with two fallbacks adds 2.
- **Targets.** `by_target` groups by the credential name recorded on the span and finds the credential id by organization and name (names are unique and cannot change). `credential_id` is `null` once the credential was deleted. Calls that never chose a target (blocked by a budget, rejected before routing) count in the totals but appear in no target.
- **Unknown values.** A ratio is `null` when its denominator is zero, `cost_usd` is `null` when no call of the key was priced, and a latency is `null` when no call measured it.

## Gateway HTTP layer: `/gw/` (added 2026-10-09)

Every error under `/gw/` uses the provider envelope of the path (OpenAI, or Anthropic for `/messages` and for `/models` with `anthropic-version`), with `X-Request-ID` and `X-Spanlight-Code`, never problem+json. The design's error table covers the gateway's own checks; these cover the HTTP layer around them.

- **Unknown path.** `404`, `spanlight_code: "NOT_FOUND"`, message `Invalid URL (<METHOD> <path>).` with the path cut to 200 characters. OpenAI type `invalid_request_error`, Anthropic type `not_found_error`.
- **Wrong method.** `405`, `spanlight_code: "METHOD_NOT_ALLOWED"`, with an `Allow` header. Type `invalid_request_error` in both envelopes.
- **Database too busy.** A pool timeout or a statement timeout while the key is checked is `503`, `spanlight_code: "SERVICE_UNAVAILABLE"`, with `Retry-After: 5`. OpenAI type `server_error`, Anthropic type `overloaded_error`. Both SDKs retry it.
- **Other HTTP statuses.** Any other status the framework raises is rendered with `spanlight_code: "ERROR"` (type `invalid_request_error` below 500, `server_error` / `api_error` from 500). A dashboard `ProblemError` raised under `/gw/` keeps its own code as the `spanlight_code`. An unexpected exception is `500 INTERNAL_ERROR` with the request id in the message.
- **`Retry-After`.** Whole seconds, rounded up, at least 1.
- **`/models`.** No `X-Spanlight-Cache` header: listing models is never cached and is not a model call. A passed-through answer carries `X-Spanlight-Attempts: 1`.
- **Compressed bodies.** A request body may be sent with `Content-Encoding: gzip` or `deflate`; the 10 MB limit applies to the decompressed size as well (`413 PAYLOAD_TOO_LARGE`). Another encoding, or a body that does not decompress, is `400 INVALID_REQUEST` with `param: null`.
- **Streams.** Streamed answers add `Cache-Control: no-cache` and `X-Accel-Buffering: no`, so no cache keeps them and no proxy buffers them. A `truncated_stream` fault ends the response with a clean end of stream after the last forwarded frame (the connection is not aborted); the stream lacks its final event (`[DONE]` or `message_stop`). Its span is recorded as failed (`status = error`, message `200 FAULT_TRUNCATED_STREAM: …`, error class `unknown`) when the fault withheld anything; a stream with no more frames than `after_chunks` passes whole and its span stays `ok`.
- **Usage chunk the client did not ask for.** A streaming chat completion without `stream_options` is sent upstream
  with `stream_options.include_usage: true`, so its span has tokens and a cost. OpenAI then ends the stream with a
  chunk whose `choices` is empty and which carries `usage`. The gateway reads that chunk for the span and does not
  forward it, so the client gets the stream it asked for (code that reads `chunk.choices[0]` keeps working). A
  client that sets `stream_options` itself, with or without `include_usage`, gets the stream unchanged.
- **Provider answers over 10 MB.** A non-streaming provider answer longer than 10 MB ends the attempt with `502
  UPSTREAM_UNREACHABLE` (message: the answer exceeds the gateway's 10 MB limit), never retried or fallen back from.
- **Non-strict JSON.** A request body with `NaN` or an infinite number is `400 INVALID_REQUEST` (`param: null`),
  with no upstream attempt: it cannot be sent as strict JSON. Its span is still recorded, with each such number
  stored as the string `"NaN"`, `"Infinity"` or `"-Infinity"`.
- **NUL and lone surrogates (added 2026-10-13).** A `model` holding NUL or a lone UTF-16 surrogate is `400
  INVALID_REQUEST` (`param: "model"`) before the call starts, with no span, like any body without a usable `model`.
  Elsewhere in the body both are forwarded as sent (a lone surrogate as its `\uXXXX` escape); the call's span
  stores the body with NUL removed and each lone surrogate replaced by `?`, as ingestion does.
- **`NO_COMPATIBLE_TARGET` names the surface.** The message names the surface (`chat_completions`, `responses`,
  `messages` or `models`), not the request path: `Route 'support' has no target that serves messages.`
- **Rate limiter fails open.** When the gateway's rate-limit buckets cannot be read (their pool is exhausted or the
  database refuses), the call is let through rather than refused, as for the dashboard's limiters; the failure is
  logged and counted in the rate-limit metrics.
- **Overhead counts from authentication.** `spanlight.gateway.overhead_ms`, the overhead metric and the span's
  start time count from when the gateway started checking the key, so authentication, limits and reading the body
  are part of the overhead. `time_to_first_token_ms` counts from the same moment. The route's `timeout_ms` budget
  still starts when the call is routed.
- **Spans can be lost under overload or a long database outage.** The gateway writes spans after the answer,
  with at most `GATEWAY_RECORD_BACKLOG` waiting, each holding its input and output already redacted and cut to 32 KB as
  stored. A failed write is retried after 0.1, 0.5 and 2 s (up to four tries; a span for a deleted project is not
  retried), each try one transaction that first checks whether an earlier try was stored after all; a span still
  not stored is counted as `spanlight_gateway_record_failures_total{reason="error"}`,
  and a span past the backlog is dropped and counted as `{reason="overflow"}`. Both also count in
  `gateway_spans_lost` on `/health/ready`. A span whose input or output cannot even be prepared for the queue is
  counted as `{reason="error"}` too, and logged by error type only. The client's answer is not affected.
- **A retried call can be billed twice.** On a stored route, a read timeout or a connection error before the first
  byte of the answer can lead to a second request for the same call, and the provider may already have received
  and billed the first one; the gateway cannot tell. A retry (by default one more attempt on the target,
  `max_attempts` up to 5) happens only while the route's `timeout_ms` budget has room for the wait. A fallback to
  the next target happens when the error matches the route's `fallback.on` conditions (by default all of them).
  The span records the usage and cost of the attempt that answered and lists every attempt in
  `spanlight.gateway.attempts`, so the failed attempt's spend, if any, is not in Spanlight's totals. A request
  that hangs uses the whole budget and is never retried, so it cannot be billed twice that way. Calls Spanlight
  makes for itself (judges, Explain with Claude, the playground, experiment items and the demo traffic job) go
  through a single-target route with one attempt and are never retried.
- **A stream cut short is priced as reported.** When a provider's stream ends before its final event (the connection
  breaks, or the stream idles out), the span takes the usage the provider had reported by then. Anthropic's
  `message_start` carries the input tokens and an output count of 1, and the real output count only comes with
  `message_delta`, so a cut Anthropic stream is priced with all its input tokens and that first output count, which
  is lower than what the provider bills. The span is `error` and its cost is not `null`. A stream that ended before
  any usage was reported has `null` usage and cost.
- **No CORS.** `/gw/` sends no CORS headers and answers a preflight `OPTIONS` with `405`, so browsers cannot call the gateway from another origin. On purpose: a gateway key is a server-side secret and does not belong in a browser.

## Alert channels (added 2026-10-10)

- **Routes.** `GET /api/v1/orgs/{id}/alert-channels` and `GET .../{channel_id}` need `org:read` (any member).
  Adding, editing, deleting and testing need `alerts:write` (admins and owners); a member is `403`, a non-member
  `404`. Channels are listed by name, unpaginated (at most 20 per organization).
- **Body.** `{kind, name, config, secret}`. `config` and `secret` are validated for the `kind` sent with them, and
  errors name `config.<field>` and `secret` (not the kind). `config` may be omitted for `slack` (`{}`) and
  `pagerduty` (`{"severity": "error"}`). `secret` is required for `slack` (the incoming-webhook URL, which must
  start with `https://hooks.slack.com/`) and `pagerduty` (a routing key of 32 letters and digits), and refused for
  `email` and `webhook`. Names are trimmed and compared exactly (`409 CHANNEL_NAME_TAKEN`).
- **Secrets are write-only.** Responses carry `has_secret`, never a Slack URL or routing key. A webhook's signing
  secret (`whsec_` and 32 base64url characters) is generated by the server and returned as `secret` only by the
  create and by a `PATCH` with `rotate_secret: true`; every other response has `secret: null` or no such field.
  Those two routes take no `Idempotency-Key` and answer with `Cache-Control: no-store`.
- **Email recipients.** 1 to 10 addresses, lower-cased and de-duplicated. Each must belong to a current member with
  a verified email (`422 RECIPIENT_NOT_MEMBER`, field `config.to`, naming the refused addresses) unless
  `ALERT_EMAIL_ANY_RECIPIENT=true`.
- **Webhook URL.** `https://` only, no user name or password, no fragment (a query string is allowed), and the host
  must resolve to public addresses only; any failure is `422 UNSAFE_URL` on `config.url`, including a host that
  does not resolve. Any `@` before the path is refused, an empty user name included.
  `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` allows `http://` and private addresses.
- **Edits.** `PATCH {name?, config?, secret?, rotate_secret?}`; the kind never changes. `config` replaces the
  whole config. `rotate_secret` on a non-webhook channel is `422` on `rotate_secret`. A new config or secret clears
  `verified_at`, since the last test reached the old target. A `PATCH` that changes nothing writes no audit event.
- **Test.** `POST .../{channel_id}/test` takes an `Idempotency-Key`. It answers `200 {delivery_id, status,
  error}` with `status` `sent`, `failed` or `pending` (a failure a retry might fix, left to the worker; also when
  the worker claimed the row first). An email channel sends to each recipient who is still a verified member,
  skipping the others; when none is left it is `422 RECIPIENT_NOT_MEMBER` and nothing is sent. `delivery_id` is
  the first row that did not go out, else the first row. A channel of another organization is `404` before the
  rate limit (10 per channel per hour, `429 RATE_LIMITED` with `Retry-After`) is consulted.
- **Errors.** `409 NOT_CONFIGURED` for a Slack, webhook or PagerDuty channel (create, new secret, rotation) when
  `CREDENTIALS_KEYS` is not set, and for testing an email channel when no email provider is configured; `422
  LIMIT_EXCEEDED` for the 21st channel of an organization.
- **Verified.** A test that goes through sets `verified_at` only if the channel was not edited while the test was
  being delivered.
- **The webhook URL is not a secret.** `config.url`, query string included, is shown to every member. Receivers
  should authenticate deliveries with the signature, not with a token in the URL.
- **Delivery log.** `GET /api/v1/orgs/{id}/alert-channels/{channel_id}/deliveries?status&limit&cursor` needs
  `org:read` and lists the channel's outbox rows newest first (`created_at`, then `id`), one row per recipient for an
  email channel. Items are `{id, kind, status, attempts, next_attempt_at, last_error, created_at, summary}` with
  `summary: {event, event_id?, rule_id?, rule_name?} | null`, read from the payload's `summary` key, which settled rows
  keep. A row's target (addresses) and payload are never returned. A channel of another organization is `404`.
- **Retry.** `POST .../deliveries/{delivery_id}/retry` needs `alerts:write` and answers the delivery as it now
  stands. Only a `failed` row of the channel can be retried: its attempts reset to 0 and it is due at once, with the
  same id (so `X-Spanlight-Delivery` is stable) and its `last_error` until the next attempt. Any other status is `409
  NOT_RETRYABLE`; so is an email whose recipient is no longer a verified member of the organization (unless
  `ALERT_EMAIL_ANY_RECIPIENT` is on), without naming the address; a delivery that is not the channel's is `404`. Audit action `alert_channel.retry_delivery`.

## Alert rules and events (added 2026-10-10)

- **Routes.** `GET /api/v1/projects/{id}/alert-rules`, `GET .../{rule_id}`, `POST .../alert-rules/preview` and
  `GET /api/v1/projects/{id}/alert-events` need `project:read`. Creating, editing, muting and deleting rules and
  acknowledging events need `alerts:write` (admins and owners).
- **Budget rules are hidden.** A budget owns a rule with `kind: budget`. It is not listed, every `/alert-rules/{id}`
  route answers `404` for it, and it does not count toward the limit of 100 rules per project (budgets have their
  own limit). Its events are listed under `/alert-events` with `rule_kind: budget`.
- **Body.** `POST` and `PATCH` take the whole rule: `{name, kind, metric, comparator, threshold, window_minutes,
  baseline_windows, sensitivity, filters, cooldown_minutes, enabled, channel_ids}`. A `PATCH` replaces the rule (its
  kind may change) and writes no audit event when nothing changed. `kind` is `threshold` or `anomaly`. A threshold rule needs `threshold` and refuses `baseline_windows`
  and `sensitivity`; an anomaly rule needs `baseline_windows`, defaults `sensitivity` to `3.0` and refuses
  `threshold`. `metric` cannot be `spend`. `filters` keys are `environment`, `provider` and `model`, values 1 to 200
  characters. `channel_ids` (at most 10, repeats dropped) must be channels of the project's organization, else `422
  UNKNOWN_CHANNEL` naming the ids on `channel_ids`. Rule names need not be unique.
- **Editing, disabling or deleting a firing rule resolves it and sends `alert.resolved`.** When a `PATCH` changes
  `kind`, `metric`, `comparator`, `threshold`, `window_minutes`, `filters`, `baseline_windows` or `sensitivity`, or
  sets `enabled` to `false`, and when a rule is deleted, its open event is resolved at that moment, its state becomes
  `ok` (so the cooldown starts) and `alert.resolved` is queued for the rule's current channels unless it is muted,
  all in the same transaction as the change. Renaming, changing `channel_ids` or `cooldown_minutes`, and enabling
  keep the state.
- **List.** Firing rules first, then by name, unpaginated. Each rule embeds `state: {state, since, last_value,
  last_evaluated_at}`, `null` before its first evaluation.
- **Mute.** `POST .../{rule_id}/mute {"until": <datetime> | null}`. `until` must be after the server's clock and at
  most 30 days ahead, else `422` on `until`; `null` unmutes. The response is the rule.
- **Preview.** The body is the rule without `name` and `channel_ids`. Points are spaced by the smallest multiple of
  `window_minutes` that is at least 60 minutes, over the last 7 days (at most 168 points: 168 for 60 or 5 minutes,
  160 for 7 (every 63 minutes), 56 for 180). The last point ends at the current minute for windows under an hour and at the start of
  the current hour for longer ones, so older points read hourly rollups. Each `value` is the metric over the rule's own window ending at
  `window_end`. An anomaly rule's `threshold` at a point is computed as evaluation computes it: from the
  `baseline_windows` back-to-back windows of `window_minutes` right before that point's window, `null` while fewer
  than 3 of them are known. Values and thresholds are decimal strings without an exponent; anomaly thresholds are
  rounded to 6 decimal places, as stored on events. At most 30 previews per person a minute (`429 RATE_LIMITED`).
  The route is a `POST`, so a personal access token needs the `write` scope: a read-scope token gets `403
  TOKEN_SCOPE`, as on every non-GET route.
- **Anomaly baseline uses the sample standard deviation.** The spec says "standard deviation" without a divisor.
  The baseline's spread is the sample standard deviation of its known windows (divisor `n - 1`, not `n`), floored
  at 10% of their mean, so a baseline of few windows is judged a little more cautiously. At least 3 known windows
  are needed, so `n - 1` is never 0.
- **Events.** `GET .../alert-events?rule_id&state=firing|resolved&limit&cursor`, newest first (`started_at`, then
  `id`), keyset-paginated. `state` is derived: `firing` while `resolved_at` is null. `POST
  .../alert-events/{event_id}/acknowledge` works on open and resolved events and answers the event; the second time
  is `409 ALREADY_ACKNOWLEDGED`.

## Budgets (added 2026-10-10)

- **Routes.** `GET /api/v1/projects/{id}/budgets` and `GET .../budgets/{budget_id}` need `project:read`; `POST`,
  `PATCH` and `DELETE` need `alerts:write` (admins and owners). The list is sorted by name and unpaginated (at most
  50 budgets per project, `422 LIMIT_EXCEEDED` past that).
- **Body.** `POST` takes `{name, scope, scope_id, period, amount_usd, action, enabled, channel_ids}`: `scope` is
  `project`, `gateway_key`, `user` or `model`; `scope_id` is absent or `null` for `project` and required (1 to 200
  characters) otherwise; `period` is `daily` or `monthly`; `amount_usd` is above 0 with at most 4 decimal places;
  `action` is `notify` or `block`. Names are unique per project (`409 BUDGET_NAME_TAKEN`). A `gateway_key` scope must
  be the id of a gateway key of the project, revoked or not (`422 UNKNOWN_SCOPE` on `scope_id`); it is stored in its
  canonical form. `channel_ids` follow the rule's rules (`422 UNKNOWN_CHANNEL`).
- **Edit.** `PATCH` is partial: fields left out are kept, `null` is refused except for `scope_id`, and `scope_id` can
  only be sent together with `scope`. Channels are checked only when sent, and the scope only when sent, so renaming
  a budget whose gateway key or channel is gone still works. An edit that changes nothing writes no audit event.
- **Periods.** UTC calendar periods: a daily budget starts again at 00:00 UTC, a monthly one at 00:00 UTC on the 1st.
  Spend is the `spend` metric (priced spans, like `cost_usd`) over the period so far, filtered by the scope only. The
  budget fires when spend is strictly above `amount_usd`, sends `budget.exceeded`, and resolves with `alert.resolved`
  on the first pass that measures it at or below the amount. A budget still firing from an earlier period resolves on
  the first pass of the new period whatever that pass measures (spend of blocked, unpriced calls is unknown), then
  may fire again if the new period's spend is already over.
- **State.** Each budget embeds `state: {spent_usd, period_start, resets_at, state, last_evaluated_at}`, `null` before
  its first evaluation. `period_start` and `resets_at` are the current period by the server's clock. When the last
  evaluation belongs to an earlier period (the first minute of a new one, or evaluation stopped), `spent_usd` is
  `null` and `state` is `ok` until the next evaluation. `spent_usd` is also `null` when spans exist and none was
  priced.
- **Edits that reset.** Changing `amount_usd`, `scope`, `scope_id` or `period` of a firing budget, disabling it, or
  deleting it resolves the alert at that moment and sends `alert.resolved` (with `budget` as it was), in the same
  transaction. Changing `scope`, `scope_id` or `period` also clears the measured spend, so `state` is `null` until
  the next evaluation, about a minute later.
- **A model with no price never fires.** Spend counts priced spans only. A call to a model with no price in the
  price table or the organization's overrides (see `unpriced-models`) has an unknown cost and adds nothing, so a
  budget whose spend comes from such calls stays at 0 (`spent_usd` is `null` while spans exist and none was
  priced), never fires and, with `action: block`, never blocks. Add a price override for the model to put its
  calls under the budget.
- **Hidden rule.** Each budget owns an alert rule (`kind: budget`) that is not reachable through `/alert-rules`. It
  is written in the same transaction as the budget, and deleting the budget deletes it with its events.

## Weekly digest (added 2026-10-10)

- **Project field.** `Project` carries `weekly_digest_enabled` (boolean, default `true`); `PATCH /projects/{id}` accepts it
  with `project:write` and records it in the `project.update` audit `changes` like the other fields.
- **Schedule and switches.** The `weekly_digest` job runs on Monday 08:00 UTC and exists only while
  `WEEKLY_DIGEST_ENABLED` is on. Without an email provider (`EMAIL_PROVIDER=console` with no `EMAIL_CONSOLE_FILE`) it ends
  `done` with the outcome `skipped_not_configured` and queues nothing.
- **Week.** The seven whole UTC days that ended at the latest Monday 00:00 (a retry on Tuesday summarises the same
  week); changes compare against the seven days before. The opening line names the first and last day of the week.
- **Who gets it.** A project qualifies when its switch is on and its hourly rollups hold at least one span in the week
  (so a span ingested in the last few minutes may not count); the demo organization is excluded. Each organization
  member with a verified email gets one message per project, except the shared demo account.
- **Copy.** An unknown figure is "—" with no change. A change against an unknown or zero earlier value reads "new",
  error rate included; when both weeks are zero the figure shows no change text. Spend and call counts change in
  whole percent, error rate in percentage points. "Top models by cost" lists up to five priced models by summed cost
  and is omitted when none was priced. The alerts line reads "No alerts fired this week.", "1 alert fired this week."
  or "N alerts fired this week.". The subject collapses line breaks in the project name.
- **Delivery.** Rows are transactional mail (`channel_id` null). Each row's `summary.digest_key`
  (`weekly_digest:<project>:<week start>`) makes a retried run skip projects already queued. A run handles about 40
  seconds of projects per attempt and has three attempts; projects left after the last attempt are logged as
  `weekly_digest_incomplete` and wait for the next week.

## Span error class, request hash and finish reason (added 2026-10-10)

- **Error class.** Spans carry `error_class` (`SpanOut.error_class`): one of `auth`, `rate_limit`, `timeout`,
  `context_length`, `content_filter`, `provider_5xx`, `network`, `client`, `unknown`, and `null` exactly when the span
  did not fail. Ingestion computes it. A numeric status decides first, read from the first of the attributes
  `spanlight.gateway.upstream_status`, `http.status_code`, `error.status` that holds a whole number (an integer, a
  whole float or a string of digits): 401 and 403 are `auth`, 429 `rate_limit`, 408, 499 and 504 `timeout`, 413
  `context_length`, any 5xx (529 included) `provider_5xx`, any other 4xx `client` unless the message says the context
  was too long or the content was filtered, which wins. Otherwise case-insensitive message patterns decide, in the
  order auth, rate limit, timeout (which includes "client disconnected" and "aborted"), context length, content
  filter, network, provider 5xx; anything else is `unknown`. The message matched is the stored (redacted) one. Spans
  ingested before the column existed keep `null` even when they failed.
- **Trace summaries.** `TraceSummary` gains `error_class`: the class of the span that supplies `error_message` (the
  earliest failed span, ties broken by span id). `null` when no span failed or that span has no class.
- **Filter.** `GET /projects/{id}/traces?error_class=<class>` keeps traces with at least one span of that class
  (not only the earliest). Any other value is `422 VALIDATION_ERROR`.
- **Request hash.** Spans carry `request_hash` (32 lowercase hex), stored but not returned by the explorer. A span
  may send its own (`SpanIn.request_hash`, 32 hex; uppercase is lowercased) and it is stored as sent. A malformed
  value is ignored, never a reason to reject the span: the server computes its own instead. Otherwise the server
  computes it from the span's `model` and `input` as received, before payload capture drops the input, so projects
  that store no payloads still get it: the first 32 hex characters of the SHA-256 of the canonical JSON (sorted
  keys, `,` and `:` separators, non-ASCII kept) of `{"model": model, "input": cleaned}`, where `cleaned` is `input`
  without the top-level keys `stream`, `stream_options`, `user`, `metadata`, `idempotency_key`, `extra_headers`,
  `extra_query`, `extra_body`, `timeout`, `api_key`, `http_client` when `input` is an object. `null` when the span
  has no input.
- **Finish reason.** `SpanOut.finish_reason` is canonical: `stop`, `length`, `tool_calls`, `content_filter` or
  `other` (`null` when the span reported none). The raw value comes from the first present of `SpanIn.finish_reason`
  (at most 64 characters), the attribute `finish_reason`, `response.finish_reasons[0]`, `response.stop_reason` and
  `gen_ai.response.finish_reasons[0]` (OTLP spans keep that attribute as sent). OpenAI `stop`, `length`,
  `tool_calls`/`function_call`, `content_filter` and Anthropic `end_turn`/`stop_sequence` (`stop`), `max_tokens`
  (`length`), `tool_use` (`tool_calls`), `refusal` (`content_filter`) map as named; `pause_turn` and every other
  value are `other`. Matching ignores case and padding. The raw value stays in `attributes`; a span that sent it only
  as `SpanIn.finish_reason` gets it as the `finish_reason` attribute.
- **Gateway spans.** A fault that answers before the upstream call (`auth_expired`, `scope_denied`, `rate_limited`,
  `unsupported_parameter`, `provider_5xx`, `timeout`) now sets `spanlight.gateway.upstream_status` to the status the
  gateway answered with, and `spanlight.gateway.retry_after_s` to its `Retry-After` (`rate_limited` only).
  `spanlight.gateway.attempts` stays `0`, which tells a simulated status from a provider's. The gateway computes the
  span's `request_hash` from the client's body and the requested model (not the model that answered, so a fallback
  keeps the hash). A provider's `Retry-After` is read from `retry-after-ms` (divided by 1000) when present and valid,
  else from `retry-after`; both headers are passed back to the client. A `malformed_json` fault answers `200`, but
  its span is recorded as failed (`status = error`, message `200 FAULT_MALFORMED_JSON: …`, error class `unknown`): the
  client received a body it cannot parse.
- **`x-spanlight-release`.** The gateway reads the header into the trace's `release`: 1 to 128 characters after
  trimming, as `TraceIn.release`; a value outside those bounds is dropped, not rejected.

## Releases

- **Scope.** A release is the `release` of a trace. `GET /projects/{id}/releases` and `GET /projects/{id}/releases/compare`
  read the raw traces that started inside `from`/`to` (default last 24 h) and all spans of those traces, for any
  window up to 30 days; a longer window is `422 RELEASE_WINDOW_TOO_LARGE` (a window over 90 days is still
  `422 VALIDATION_ERROR` first). There is no rollup path, so percentiles are always exact. `environment` filters on the
  trace's environment. Both routes are also readable by an API key with `traces:read`, for its own project.
- **`ReleaseStats`.** `traces` counts the release's traces in the window; `first_seen_at`/`last_seen_at` are the
  earliest and latest trace start. `llm_calls`, `unpriced_calls`, `error_rate` (`null` without calls), `p50_ms` and
  `p95_ms` are over spans of kind `llm`; `cost_usd` and token totals are over all spans of those traces, the same
  definitions as the metrics overview. `cost_usd` (a decimal string) is the sum of the priced spans: `null` only when
  nothing was priced, and a lower bound while `unpriced_calls` is above 0. `unpriced_calls` is an addition to the
  spec's field list. The list holds at most 200 releases, the one seen last first.
- **`compare`.** `a` is the baseline and `b` the candidate; `a == b` is `422 SAME_RELEASE` (checked before the lookup)
  and a release with no traces in the window is `404 UNKNOWN_RELEASE`. `deltas` has one entry per headline figure
  (`traces`, `llm_calls`, `error_rate`, `p50_ms`, `p95_ms`, `cost_usd`, `input_tokens`, `output_tokens`) with
  `absolute = b - a` and `relative = absolute / a` (rounded to four places), both decimal strings; the entry is `null`
  when either side is unknown and `relative` is `null` when `a` is 0.
- **`model_mix`.** Each model's share of each side's LLM calls (`llm_calls` of that release; at most the 100 busiest
  models per side are listed); a side without calls has `null` shares, and a model absent from a side has share `0`.
  A span without a model is the entry with `model: null`.
- **`error_classes`.** Exact counts of failed spans (any kind) per `error_class`, `null` for failed spans that carry no
  class (ingested before the column existed).
- **`new_errors`.** Every failed span's status message of the two releases is normalised in the database (UUIDs and hex
  ids of 12 or more digits become `…`, other digit runs `#`, whitespace collapsed, at most 200 characters). The top 10
  messages that at least one failed span of `b` has and no failed span of `a` has are returned, most frequent first,
  with the number of `b` spans as `count` and the id of one `b` trace as `example_trace_id`.

## Insight notifications (added 2026-10-10)

- **Payload.** `insight.opened` follows the alert payload's conventions (`version`, `event_id`, `occurred_at`, `org`,
  `project`, `url`; datetimes `YYYY-MM-DDTHH:MM:SSZ`). `insight` carries one addition to the spec's field list:
  `fingerprint`, the stable 32-hex name of the problem within the project, which PagerDuty's `dedup_key`
  (`spanlight-insight-<fingerprint>`) needs at send time. `evidence.metrics` values are decimal strings, integers or
  `null`. `occurred_at` is the detection that opened the insight (its `last_seen_at`).
- **Link.** `url` is `<APP_BASE_URL>/<org_id>/<project_id>/doctor/<insight_id>`: the app's routes are keyed by ids, as
  the alert payload's links are, not by slugs.
- **Who is told.** Only an insight that opens (new, reopened, or a mute that ended) with severity `critical` notifies,
  once per channel in the project's `insight_channel_ids`; a channel deleted since is skipped. PagerDuty gets
  `trigger` with severity `critical` whatever the channel's severity, and never `resolve`.
- **Delivery log.** `summary` gains `insight_id`, `project_id` and `title` for `insight.opened` rows (`rule_id` and
  `rule_name` are `null` on them). A channel belongs to the organization, so its log lists rows from every project
  that uses it; `project_id` lets a client link to the insight in the right project. Rows queued before it was kept
  lack it (`null`).
- **Weekly digest.** "Open" in the Insights section means status `open` or `acknowledged`, as in the health score
  (muted and resolved insights are not counted). Listed insights are critical first, then the most recently seen. The
  section is shown when the project has an open insight or one first seen during the week; a reopened insight counts
  through the first condition only.
- **Evidence decimals.** `evidence.metrics` decimals are written in the canonical form everywhere (stored evidence,
  API and payload): normalised, no exponent, zero as `"0"`.

## Insights (added 2026-10-10)

- **Reads.** The list, one insight, the summary and the detector runs need `project:read` and are not open to API keys;
  the actions (acknowledge, resolve, mute and unmute) and `explain` need `insights:manage` (owner, admin). A non-member gets `404`.
- **Filters.** `status` may repeat (`?status=open&status=acknowledged`); `severity`, `kind` and `trace_id` take one
  value. `trace_id` keeps insights whose `evidence.trace_ids` contains the id exactly. An unknown `status` or `severity`
  is `422`. The cursor is the `(last_seen_at, id)` of the last item.
- **Summary.** `open_critical`, `open_warning` and `open_info` count insights in status `open` or `acknowledged` (the ones
  that still need a person), despite the `open_` prefix. A muted insight is not counted, even with its mute ended.
- **Evidence.** `metrics` values are decimal strings, integers or `null`. `label` is the catalogue's name for the kind, or
  the kind itself when the catalogue no longer has it.
- **Detector rules the spec left open.** The per-hour baselines (`cost_usd_per_hour`, `llm_calls_per_hour`) divide by
  the baseline period's active hours, those with at least one call, not by every hour, so a diurnal or batch
  workload is compared with its own busy hours. `retry_storm` only counts spans of one flow (the same trace, or traces
  that share a non-null session): identical prompts from unrelated traces are not a retry. `client_timeout_misconfigured`
  compares the abort cluster with the model's successes in the environments of the clustered aborts, leaving out
  fault-injected and cache-hit successes. Tool-span input hashes are SHA-256 of the stored jsonb's text form, computed
  in the database; they are compared only with each other, never with a request hash. Every detector returns its
  findings in fingerprint-key order.
- **Mute.** `until` must carry a UTC offset (`422` otherwise, the usual validation error); a past or too distant value, or an
  empty, blank or over-500-character reason, is `422 INVALID_MUTE`. A mute may be set from any status, including to change the end or the reason of one
  that is already muted.
- **Detector runs.** `limit` is 1–100 (default 100), newest first. `findings` is `null` when the run failed and `error`
  says why.
- **Detail.** `explanations` lists the insight's explanations that have an answer, newest first, at most 20; one still
  being written, or a paid call that returned no answer, is not shown.
- **Explain.** `POST /projects/{id}/insights/{insight_id}/explain` answers `201` with the explanation; it needs no
  `Idempotency-Key` (a repeat is a second, separately budgeted call). Checks run in this order: `404` for an unknown
  insight, `409 NOT_CONFIGURED` (budget `0`, no `CREDENTIALS_KEYS`, no Anthropic credential in the organization),
  `409 EXPLAIN_MODEL_UNPRICED` (also when the model's price, an organization override included, is zero),
  `402 EXPLAIN_BUDGET_EXCEEDED`, then the call (one attempt, no fallbacks; `502 EXPLAIN_FAILED` when it fails or its
  answer has no text). A failed call stays in the month's spend as an explanation without text, which the detail never lists,
  whenever the provider may have billed it: at the cost of its reported usage, or at the worst case after a timeout,
  a connection error, an answer without usage or the client going away once the request was sent. Only a call the
  gateway never sent (refused before the provider, such as a project `block` budget) or one the provider answered with
  an HTTP error status and no usage is not counted. Each excerpt is cut to 2048 characters and each payload to its first 1024 characters of compact
  JSON; every field is redacted before it is cut. The excerpt of a trace is its first failed span (llm spans first), else its first llm span,
  else its first span; a cited trace with no stored span is left out. `cost_usd` is the cost of the answer's token usage;
  when the provider reported no usage it is the reserved worst case, so the budget never counts a call as free. A
  project's `block` budgets apply to the call like any gateway call (a blocked call is `502 EXPLAIN_FAILED`). An insight
  deleted while the call ran answers `404`. A reservation still open after 10 minutes (its request died before it
  settled, for example a process killed mid-call) is completed by the cleanup job at its worst-case cost, without text;
  it is never deleted, because the provider may have billed it.
- **Health.** `GET /projects/{id}/health` needs `project:read` (no API keys). `from`/`to` default to the last 24 h like the
  metrics routes; the baseline is the window of equal length before it. `value` is `null` when the window has no LLM calls
  (and `components` is then empty). Components are `findings`, `errors`, `latency` and `cost`; `observed` and `baseline` are
  decimal strings or `null` (`findings` observes the count of open critical and warning insights; `findings` and `errors`
  have no baseline). `error_rate` is a fraction. `approximate` is true when any metric read was a histogram estimate.

## Users (added 2026-10-10)

- **Reads.** `GET /projects/{id}/users` and `GET /projects/{id}/users/{external_user_id}` need `project:read` and are also
  open to an API key with `traces:read` for its own project, like the metrics routes. A non-member gets `404`.
- **Source.** The figures come from `user_stats_daily`, one row per UTC day and end user (`traces.external_user_id`), which the
  `refresh_user_stats` job recomputes for yesterday and today every 15 minutes for projects with a user-tagged trace since
  the start of yesterday, UTC (`USER_STATS_ENABLED`). A trace counts for the day it started. `llm_calls`, `errors` and `tokens` are over the
  trace's spans of kind `llm` (an error is a span with status `error`, so `errors` differs from the `error_count` of `recent_sessions`, which counts
  failed spans of every kind; tokens are input plus output, input already
  including cached tokens). Days older than yesterday are only as complete as the job was when they were current: there is
  no backfill. Rows older than the project's `retention_days` are deleted by the retention job.
- **Window.** `from`/`to` (default last 24 h, at most 90 days) are widened to whole UTC days: `from` floored, `to` ceiled.
  The response's `window` (`start`, `end`, midnights in UTC, `end` exclusive) says which days were summed.
- **`cost_usd`.** The sum of the priced calls over the days with activity: `null` when calls were made and none was priced,
  a lower bound when only some were (`unpriced_calls` counts the calls without a price, as on releases). A user (or a day) with no LLM call has cost `"0"`.
- **List.** `sort` is `cost` (default), `errors` or `traces`, always descending, users without a value (unknown cost) last,
  then by `external_user_id` ascending. `limit` is 1–200 (default 50). The cursor encodes the sort value and the id of the
  last item; one issued for another `sort` is not rejected but pages meaninglessly. A cursor that does not decode is
  `422 VALIDATION_ERROR`, like every other keyset cursor, not the `INVALID_CURSOR` the spec named. `first_seen_day`
  and `last_seen_day` are the first and last day in the window with activity.
- **Detail.** `daily` has one entry per UTC day of the window: a day with no traces is all zeros with `cost_usd` `"0"`, a day
  with calls but none priced has `cost_usd` `null`. `recent_sessions` is the user's 20 sessions with the latest activity,
  over all time (not limited to the window), in the shape of `GET /sessions`. A user with no activity in the window is
  `404 NOT_FOUND`. The id is URL-encoded by the client; a `/` in it is accepted. Browsers resolve `.` and `..` path
  segments even when percent-encoded, so the id has a one-character escape: the client prefixes `~` when the id is `.`
  or `..` or starts with `~`, and the route strips exactly one leading `~` before the lookup (`~~a` names the user
  `~a`, `~.` names `.`, `a` names `a`). A path with NUL is `422 VALIDATION_ERROR`.

## Prompts, scores, datasets, evaluators and experiments (added 2026-10-11)

These routes follow the conventions above (problem+json, keyset pagination, decimal-string money, `null` for an
unknown value). Dashboard routes are under `/api/v1/projects/{project_id}`; `POST /v1/scores` and
`GET /v1/prompts/{name}` are key-authenticated routes beside the ingestion ones. Permissions: viewers read;
members hold `prompts:write`, `datasets:write`, `scores:write`, `annotations:write` and `experiments:run`;
`evaluators:write` is admin and owner only (a judge spends money unattended). A personal access token with the
`read` scope can use none of the write permissions.

**New problem codes.** `NAME_TAKEN`, `KIND_MISMATCH`, `NO_CHANGES`, `PROMPT_IN_USE`, `DATASET_IN_USE`,
`SCORE_CONFIG_IN_USE`, `RUN_IN_PROGRESS`, `ITEM_NOT_ASSIGNED_TO_YOU`, `ITEM_NOT_PENDING` (all 409);
`LABEL_RESERVED`, `DATASET_FULL`, `SCORE_NOT_IN_QUEUE`, `SCORE_REQUIRED`, `UNKNOWN_SCORE_CONFIG`,
`SCORE_FILTER_NEEDS_NAME`, `BUDGET_REQUIRED`, `RULE_NEEDS_EXPECTED_OUTPUT`, `CHANNEL_KIND`,
`JUDGE_TEMPLATE_VARIABLES`, `EVALUATOR_MODEL_UNPRICED`, `EXPERIMENT_MODEL_UNPRICED`, `TARGET_OVERRIDE_INVALID`
(all 422); `UNSUPPORTED_FILE` (415); `EVALUATOR_BUDGET_EXCEEDED` (402); `EVALUATOR_FAILED` (502).

### Prompts

- **Names.** A prompt name is 1 to 128 characters, case-sensitive and unique in the project (`409 NAME_TAKEN`). It
  is segments joined by `/`; every segment matches `[A-Za-z0-9][A-Za-z0-9_.-]*`. A segment starting with a letter or
  digit means `.` and `..` can never be a segment, which matters because HTTP clients and browsers resolve those
  before sending. A segment may not be `versions` or `labels`, the words that follow a name in the routes, so
  `a/versions/b` is never ambiguous (`422 VALIDATION_ERROR`). The database repeats both rules in a CHECK.
- **The `~` escape.** The dashboard routes strip exactly one leading `~` from the name segment before the lookup,
  the same helper the Users routes use. No valid prompt name starts with `~` or `.`, so the escape is never needed
  for one; it is kept so the two families of routes behave alike. The key route `GET /v1/prompts/{name}` does not
  strip it.
- **A prompt keeps its kind.** A version whose `template.kind` differs from the prompt's is `409 KIND_MISMATCH`.
  `POST /prompts` repeats the kind as `kind`, and `kind` must equal `template.kind` (`422`).
- **No duplicate versions.** A new version whose `template` and `model_config` equal the latest version's is
  `409 NO_CHANGES`. At most 100 versions per prompt per UTC day; the 101st is `429 RATE_LIMITED` with `Retry-After`
  set to the seconds until midnight UTC.
- **Versions are immutable.** The database rejects any `UPDATE` of a `prompt_versions` row; a delete of the whole
  prompt cascades. `variables` is filled by the server and cannot be sent.
- **Labels.** A label is `[a-z0-9][a-z0-9_-]{0,31}`. `PUT …/labels/latest` is `422 LABEL_RESERVED`: `latest` always
  means the highest version. Pointing a label at a version of another prompt, or deleting a label that does not
  exist, is `404`.
- **`PROMPT_IN_USE`.** Deleting a prompt is `409 PROMPT_IN_USE` while an evaluator's `config.prompt_version_id` or an
  experiment's `config.target.prompt_version_id` names one of its versions.
- **Reading with a key.** `GET /v1/prompts/{name}?label=|version=` needs the `prompts:read` scope (`403 KEY_SCOPE`
  without it) and shares the per-key request limit with ingestion. `label` defaults to `production`. **`latest`
  is accepted here as a read alias** for the highest version, although it cannot be set as a label. `label` and `version`
  together are `422 VALIDATION_ERROR`. An unknown name, label or version, and a name that exists only in another project,
  are `404 NOT_FOUND`. The response is `{name, version, version_id, kind, template, variables, model_config, labels}`
  with `ETag: "<version_id>-<label hash>"` and `Cache-Control: private, max-age=60`; a matching `If-None-Match` (weak or
  strong, a list, or `*`) is an empty `304` with the same headers. **The spec's ETag was the version id alone;** the
  body lists the version's labels, so the tag adds the first 12 hex digits of the SHA-256 of the sorted labels, and
  moving a label onto or off the served version changes it.
- **Compiled size.** A compiled prompt (the text, or all messages together) is at most 256 KB of UTF-8, checked before
  anything is rendered, so a small template repeating a large variable cannot allocate hundreds of megabytes. Past it
  the server raises `TemplateTooLargeError` (the SDK raises the same name): a playground config gets the error
  `prompt_too_large`, an experiment item the item error `prompt_too_large`, and a judge run fails with
  `judge_prompt_too_large`. The shared template fixture has no case near the cap.
- **`GET /prompt-versions/{version_id}`.** The dashboard route that reads a version by its id, with the name of its
  prompt (`project:read`). Evaluators and experiments store version ids, so a client needs it to show their names. A
  version of another project's prompt is `404`.
- **Usage.** `GET …/versions/{version}/usage` covers the last 7 days of raw spans. `cost_usd` and `p95_ms` are
  `null` when unknown; `has_unpriced` says some LLM calls had no cost, which makes `cost_usd` a lower bound.
- **Spans.** A span may carry `prompt_version_id` (native JSON ingestion only). There is no foreign key: the id of a
  version the server has not seen, or that was deleted, is stored as sent. `GET …/traces` accepts
  `prompt_version_id` to keep traces with a span of that version.
- **The SDK's one exception.** `spanlight.prompts.get` raises `PromptUnavailableError` (a plain `Exception`) when
  nothing is cached, the fetch failed (including `401`, `404` and `5xx`) and no valid `fallback` was passed, and also,
  without a request, for a name that is empty or has an empty, `.` or `..` segment. Every other SDK call still
  never raises into your code.

### Scores

- **Implicit score configs.** A score with a name that has no config creates one (`implicit: true`) with the type
  the first score implies: `string_value` is categorical, a JSON `true`/`false` boolean, any other number numeric,
  with no range and no category list. Two first scores racing agree on one config (the loser re-reads it). Editing
  a config, or creating one in the dashboard, makes it explicit. `POST /score-configs` for a name that already has
  a config, implicit or not, is `409 NAME_TAKEN`.
- **Config changes.** `PATCH /score-configs/{id}` takes `data_type`, `min_value`, `max_value`, `categories` and
  `description` (not `name`), and the result is validated as a whole config. Changing `data_type`, or deleting the
  config, is `409 SCORE_CONFIG_IN_USE` while any score carries the name. A new range or list applies to new scores
  only. Review queues keep config ids without a foreign key; deleting a config that a queue lists is also
  `409 SCORE_CONFIG_IN_USE`, since its items could never be completed.
- **At most 500 score names per project.** A score whose new name would create the project's 501st config is rejected on
  its own (`name: too many score names in this project (at most 500)`), and so is `POST /score-configs`
  (`422 VALIDATION_ERROR`). `GET /score-configs` stays unpaginated and returns at most 500 configs; concurrent writers
  can pass the cap by a few rows, which the list then leaves out.
- **NUL and lone surrogates.** NUL is removed from `string_value`, `comment` and `span_id` before validation, and a lone
  UTF-16 surrogate is replaced by `?`, because PostgreSQL refuses both and one bad score would otherwise fail its batch's
  insert. A `string_value` that is empty afterwards is rejected on its own. Judge reasoning gets the same treatment.
- **Primary key and client ids.** A score's key is `(project_id, id)`, not `id` alone. With a global key, two tenants
  who happened to choose the same `id` would collide invisibly (row-level security hides the other tenant's row, so
  `ON CONFLICT DO NOTHING` would silently drop the second score). A client-supplied `id` (a UUID) makes a retry
  idempotent: a repeat counts as `accepted` and stores nothing. On `POST /traces/{trace_id}/scores` a repeated `id`
  returns the stored score as it is, even if it belongs to another trace or author.
- **Values.** Score values are JSON numbers within ±1e9, rounded to four decimals (half to even, so `1.23456` is
  stored as `1.2346`), and are serialised as JSON numbers (not strings) in every response. A JSON boolean is
  accepted in `value` and stored as `1` or `0`; it makes an implicit config boolean, and a boolean config also
  accepts the numbers `0` and `1`. Exactly one of `value` and `string_value` is allowed. A name is 1 to 64
  characters `[a-z0-9][a-z0-9_.-]*`.
- **Batch results.** `POST /v1/scores` takes 1 to 1000 scores. An empty, oversize or misshapen body is `422`. A
  score that fails validation is listed in `rejected` as `{index, reason}` with `reason` written
  `<field>: <message>`, and never fails the others. The body is read after the key is checked, bounded like span
  ingestion and may be gzip or deflate encoded. The `trace_id` need not exist yet (a score may arrive first), but
  `POST /traces/{trace_id}/scores` on the dashboard is `404` for a trace the project does not have.
  `GET /traces/{trace_id}/scores` is `200` for such a trace, on purpose: it lists the scores that already name it,
  which is `[]` when there are none, so scores sent ahead of their trace stay readable.
  A score sent with an `id` the project already stores is counted in `accepted` and ignored: the first write wins,
  also when `value`, `string_value` or `comment` differ, so a retry can never overwrite. To change a score, delete it
  (`DELETE /api/v1/projects/{project_id}/scores/{score_id}`, see Deleting a score) and send the corrected one.
  Only a signed-in person can delete: a score a person wrote on the dashboard by its author or a project admin, any
  other score (one sent through `/v1/scores` included) by a project admin. A project API key cannot delete
  (`403 KEY_SCOPE`), so a client that only holds an API key cannot correct a score.
- **Traces flag.** `traces.has_scores` is set when a score is written and never cleared, so after the last score of a
  trace is deleted `has_scores=true` still keeps the trace. It is a filter hint. A trace upsert keeps the flag when
  it is already set, and a small sweep inside the rollup job (every 5 minutes, scores of the last 15 minutes) sets it on
  traces whose first score raced the trace's own ingestion, so the flag can lag by a few minutes.
- **Retention.** Scores, online evaluator runs and review-queue items follow the project's `retention_days`, deleted by
  the hourly retention job on the traces' cutoff (by `created_at`, or `started_at` for runs), since a comment or a judge's
  reasoning can quote the trace. Online runs are kept until their UTC month ends because the evaluator's budget sums
  them, and queued or running ones are left to the reaper. Datasets, experiments and their items are kept.
- **Trace filters.** `score_min`, `score_max` and `score_value` apply to the **latest** score of `score_name` (highest
  `created_at`, ties by the higher id) and are `422 SCORE_FILTER_NEEDS_NAME` without `score_name`. `has_scores` is
  true or false. A trace in the list carries `has_scores` and up to 10 `scores` (`name`, `value`, `string_value`).
- **Deleting a score.** Your own `human` score, or any score as a project admin; others are `403 FORBIDDEN`. Deleting
  a score is logged but has no audit action.

### Datasets and review queues

- **Names.** Prompt, score-config, dataset, annotation-queue, evaluator and experiment names are each unique per
  project (`409 NAME_TAKEN`). Dataset names are trimmed and 1 to 100 characters; `GET /datasets?name=` is an exact
  match, the handle the CLI and the Action use.
- **Dataset limits.** 10 000 items per dataset (`422 DATASET_FULL`, nothing stored); `input` and `expected_output`
  at most 64 KB each and `metadata` at most 8 KB as compact JSON; NUL is removed from strings. `input` may be any JSON
  except `null`.
- **Upload.** `.jsonl` or `.csv` (`415 UNSUPPORTED_FILE`), UTF-8 with an optional byte order mark. The file is at most 5 MB:
  **`413 PAYLOAD_TOO_LARGE`** past that (the request body limit of this one route is 5 MB plus 64 KB). A bad line is not an
  error of the request: it is reported in `rejected` as `{line, reason}` (at most 100), and the rest is accepted. A reason
  never repeats the line. CSV needs a header with `input`; an unreadable record ends the file there. Parsing runs in a
  thread pool and stops once a dataset would be full.
- **From traces.** `POST …/items/from-traces` takes 1 to 200 trace ids (32 hex digits, upper case accepted and lowered;
  a malformed id is `422` for the request) and lists each trace that gave no item in `rejected`
  (`trace not found`, `no llm span`, `span not found`, `no payload stored`) instead of failing the request. Adding
  traces to a **review queue** differs: if any trace id is not a trace of the project, the **whole batch is refused**
  (`422 VALIDATION_ERROR` naming each unknown trace) and nothing is queued.
- **In use.** `DELETE /datasets/{id}` is `409 DATASET_IN_USE` while an experiment names the dataset.
- **Queues.** A queue lists 1 to 20 score configs (`422 UNKNOWN_SCORE_CONFIG` for one the project lacks) and all of them are
  required when an item is completed (`422 SCORE_REQUIRED`; a name outside them is `422 SCORE_NOT_IN_QUEUE`). A trace (or
  a trace and span) already in the queue is skipped and counted in `skipped_duplicates`.
- **Claim.** `POST …/claim` gives the lowest-id `pending` item that is unassigned, already the caller's, or claimed over
  30 minutes ago, or `204` when there is none. Completing, skipping and releasing need the item to be the caller's
  (`409 ITEM_NOT_ASSIGNED_TO_YOU`) and still `pending` (**`409 ITEM_NOT_PENDING`**). A completion that fails writes no
  score.
- **Row-level security through the parent.** Tables without a `project_id` (prompt versions and labels, dataset items,
  queue items, evaluator runs, experiment runs and items) have no `project_id` column, so their policies are `EXISTS`
  subqueries through the parent that does (prompts, datasets, queues, evaluators, experiments), plus the usual worker
  bypass. Like every project table they have row-level security both enabled and forced, so a forgotten filter returns
  nothing instead of another tenant's rows.

### Evaluators

- **Shape.** `config` is a discriminated union on `kind`: `heuristic` (with a `check` holding one `rule`), `llm_judge`
  (`prompt_version_id`, `credential_id`, `model`, `max_tokens` 1 to 4096 defaulting to 256, `temperature` unset by default)
  or `webhook` (`channel_id`). A name follows the score-name rule, because the scores it writes carry it.
  `PATCH` takes `name`, `enabled`, `sampling_rate`, `filters`, `monthly_budget_usd` and a partial `config` that merges
  into the stored one and cannot change `kind`.
- **`judge_template` alternative.** When creating or patching a judge, `judge_template` (`relevance`, `faithfulness`,
  `toxicity` or `format_compliance`) may be sent **instead of** `prompt_version_id` (exactly one of them). The server saves
  the built-in template as the chat prompt `judges/<name>` version 1 on first use and stores that version's id, so a
  stored judge always names a prompt version.
- **Checks.** A judge needs a positive `monthly_budget_usd` (`422 BUDGET_REQUIRED`), a chat prompt version of the project
  using only the variables `input`, `output` and `expected_output` (`422 JUDGE_TEMPLATE_VARIABLES`), an Anthropic or
  OpenAI credential of the organization, and a model with a positive price (`422 EVALUATOR_MODEL_UNPRICED`).
  `exact_match` and `levenshtein` with `sampling_rate > 0` are `422 RULE_NEEDS_EXPECTED_OUTPUT`. Yes/no rules (`regex`,
  `json_valid`, `contains`, `exact_match`) write boolean scores; `length` and `levenshtein` numeric ones.
  **`levenshtein` compares at most 2 000 characters of each side** (the spec said 10 000): the pure-Python distance
  takes about 9 seconds at 10 000, and it runs in a thread so it never blocks the worker's event loop. A webhook evaluator on a
  channel that is not a webhook channel of the organization is `422 CHANNEL_KIND`. A prompt version, credential or
  channel the caller cannot see is `404`, never a hint that it exists elsewhere.
- **`evaluator_runs.source` and uniqueness.** A run's `source` is `online`, `test` or `experiment`. A partial unique
  index on `(evaluator_id, trace_id, coalesce(span_id, ''))` **where `source = 'online'`** makes a rescan of the
  sampler's overlapping windows queue nothing twice, while tests and experiment items may repeat a trace. All three
  sources write real run rows, so every judge call counts against the evaluator's monthly budget.
- **Budget.** The month is the UTC calendar month by `started_at`. Before a judge call the run reserves an estimate
  (`ceil(ASCII characters / 3) + non-ASCII characters` input tokens plus `max_tokens` output tokens, at the model's price) under
  an advisory lock per evaluator. Admitted when settled spend + reservations of running runs + the estimate
  ≤ the budget; otherwise the run is `failed` with `budget_exceeded` and no request is sent. A `running` run with no heartbeat
  for 10 minutes becomes `failed`, `abandoned`, charged its reservation. `GET …/budget` returns `monthly_budget_usd`,
  `spent_usd`, `reserved_usd`, `remaining_usd` (null without a budget) and `month_start`.
- **Test.** `POST …/{id}/test` with `{trace_id}` evaluates the trace's last LLM span now and returns `{scores, cost_usd,
  reasoning}` without storing scores. A judge's test reserves like a sampled run. Over budget it is
  **`402 EVALUATOR_BUDGET_EXCEEDED`**; any other failure is **`502 EVALUATOR_FAILED`** with the run's error code in `error`.
  `404` for a trace not in the project; `422` for one with no LLM span or no stored input or output.
- **Sampling volume.** `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` defaults to 50 runs per evaluator per minute (evaluation jobs
  share the worker's short-job lane with alerts and rollups), and a judge whose committed spend already reaches its
  monthly budget is not sampled at all. A queued online run whose job died is failed `abandoned` by the reaper instead of
  staying queued for ever (the trace is not sampled again for that evaluator).
- **A run that outlives its row.** A judge call that comes back after its run was failed `abandoned` by the reaper
  changes nothing and is counted once, where the reaper counted it. A call that comes back after its evaluator or
  project was deleted has no run row to settle: it is counted once as `failed` with the error `run_deleted`, and
  the cost it would have been charged is added to `spanlight_evaluator_cost_usd_total`.
- **Never sampled.** The sampler skips traces in the environments `evals`, `playground`, `experiments` and `doctor` and
  traces tagged `spanlight:judge`, and traces that ran longer than an hour. Online runs have no expected output.
- **Webhook evaluators.** The request carries `X-Spanlight-Timestamp` and `X-Spanlight-Signature` (the alert webhook
  signature, same secret) but no event or delivery header: there is one attempt and no retry.

### Experiments and the playground

- **Targets.** `config.target` is `{kind: "prompt_version", prompt_version_id, credential_id, model, params?}` or
  `{kind: "messages", credential_id, model, messages, params?}`. `max_cost_usd` is above 0 and at most 1000 (default 5);
  `max_items` defaults to 1000 and may not exceed `EXPERIMENT_MAX_ITEMS` (`422`). An unpriced model is
  `422 EXPERIMENT_MODEL_UNPRICED`. An experiment cannot be edited or deleted.
- **One run at a time.** `POST …/{id}/runs` answers `202` with a `queued` run and supports `Idempotency-Key`. A second
  run while one is queued or running is **`409 RUN_IN_PROGRESS`**, enforced by a partial unique index. A run whose worker died is
  failed (`abandoned`) first, so it does not block new runs forever.
- **`target_override`.** The optional body `{"target_override": {"prompt_version_id"}}` runs a prompt-version experiment with
  another version of the **same prompt**, keeping its credential, model and parameters; anything else is
  **`422 TARGET_OVERRIDE_INVALID`**. Every run stores its effective target (`experiment_runs.target`) and returns it as `target`.
- **Run `number`.** Runs have a stored `number`: 1, 2, 3 in the order they started, unique per experiment and never reused.
  The experiment list carries `run_count` and `last_run`.
- **Cost limit.** The limit covers the target's calls only and is reserved: before each item, the cost so far plus the
  estimates of the calls in flight plus this item's estimate must stay within `max_cost_usd`, else the run ends `failed`
  with `cost_cap_reached` and its stored items stay. Attached evaluators spend under their own monthly budgets; an
  evaluator over budget leaves that item's score out and does not fail the item or the run.
- **Totals.** `cost_usd` is the sum of the known item costs, `null` when none is known, and `has_unpriced` says it is a lower bound.
  Latency percentiles are nearest-rank over answered items. A numeric score reports `mean` and `count`; a categorical one
  reports `categories` counts and a `null` mean. Run lists carry the totals stored when each run ended (`null` before); one
  run's read computes them live.
- **Compare.** `GET …/runs/{a}/compare/{b}` returns deltas `b − a` at four decimals; for a boolean score the values count as 1
  and 0. Higher is better for every score, so a positive delta is an improvement. A categorical score has no delta (equal is
  `unchanged`). An item present in one run has a `null` side and no deltas and is listed after matched items.
  `cost_delta_usd` and `latency_p95_delta_ms` are `null` when either side is unknown. Either run unfinished is
  **`409 RUN_IN_PROGRESS`**; a run that is not a run of this experiment is `404`.
- **Playground.** `POST …/playground/runs` (`experiments:run`, supports `Idempotency-Key`) takes 1 to 3 configs and
  answers `200` with one result per config. One failing config yields `error` on its own result only (`BUDGET_EXCEEDED`,
  `missing_variables: a, b`, or the failure); `ttft_ms` is always `null`. A credential outside the organization or a version outside
  the project is `404` for the request. It is limited to 30 runs a minute per user (`429 RATE_LIMITED`) and has no spend cap
  beyond the project's blocking budgets.

## Full test pass 2026-10-13

- **A body not fully sent.** A client that disconnects before its request body is complete is answered `400 BAD_REQUEST` ("The request body was not fully sent.") on `POST /v1/traces`, `/v1/otlp/traces` and `/v1/scores`, and `400 INVALID_REQUEST` with `param: null` ("Request body was not fully sent.") under `/gw/`. Nobody reads the answer; it keeps a dropped upload from being logged as a server error.
- **Cut or empty compressed bodies.** A `gzip` or `deflate` body whose stream ends before its end marker is `400 BAD_REQUEST` ("The request body could not be decompressed.") on the ingestion routes and `400 INVALID_REQUEST` under `/gw/`, even when the part that arrived would parse. An empty body sent with `Content-Encoding: gzip` or `deflate` has no end marker either, so it gets the same answer (it was `400 INVALID_JSON` on ingestion).
- **Card numbers in payloads.** Redaction searches card-shaped runs: digit groups separated by single spaces or dashes, every group of four or more digits but the last, which has two or more. Any 13-19 digit stretch of whole groups that passes the Luhn check is replaced, so a card next to a CVV, an expiry or a quantity is caught and the neighbouring digits are kept. Lists of small numbers, dates and phone numbers are left alone; a card typed in groups of one to three digits, or glued to a word, is not detected.

## Owner decisions 2026-10-13

Calls made after the full test pass on behaviour the spec left open.

- **Model names: whitespace stripped, case kept, pricing case-sensitive.** Every span (`POST /v1/traces`,
  `POST /v1/otlp/traces` and the gateway's own spans) has the whitespace around its `model` stripped once, at
  ingest, and the stripped name is what is stored; a model that is only whitespace is stored as `null`. Case is
  kept, and the price lookup now compares the name as stored, case included, as alert rules, rollup filters and
  budgets already did. So `" gpt-4o "` is stored as `gpt-4o` and priced, while `GPT-4o` is stored as sent, costs
  `null` ("—"), sets the trace's `has_unpriced` and appears among the project's unpriced models. Before this,
  pricing lower-cased the name and so priced `GPT-4o` while a budget or alert on `gpt-4o` did not count it. Price
  overrides are still stored lower-case, so an override cannot price an upper-case model name; send the
  provider's own (lower-case) model id.
- **OTLP values that do not fit.** On `POST /v1/otlp/traces` a resource's `deployment.environment(.name)` longer
  than 64 characters or `service.version` longer than 128 is dropped (the trace field stays `null` or keeps an
  earlier value) and the span is kept. A token count sent as a whole double (`12.0`) is stored as the integer
  (`12`); one with a fraction, or NaN or an infinity, is dropped, so the span is kept with that count unknown and
  its cost `null`. A count sent as the string `"NaN"`, `"Infinity"` or `"-Infinity"` cannot be told apart from
  such a double once decoded and is dropped the same way. One warning, `otlp_values_dropped`, is logged per batch with the number of values of each kind
  dropped, never the values. Everything else that does not fit (an over-long `user.id` or `session.id`, a
  negative or too-large count, any other count sent as a string) still rejects the span with a per-span reason, as on
  `POST /v1/traces`, which stays strict: there an over-long `trace.environment` or `trace.release`, or a
  `usage` count that is not a JSON integer, rejects the span.
- **`POST /v1/traces` reads any content type.** The body is parsed as JSON whatever `Content-Type` says
  (`text/plain`, a form type, or none), so loosely labelled clients keep working. The route needs a bearer API key,
  so a cross-site form cannot reach it. `POST /v1/otlp/traces` still answers `415 UNSUPPORTED_MEDIA_TYPE` for a
  type other than `application/json` or `application/x-protobuf`, because it must know which encoding it was sent.
- **Card numbers sent as JSON numbers are not redacted.** Redaction reads strings only. A Luhn-valid 13-19 digit
  JSON integer (`{"card": 4242424242424242}`) is stored as sent: about one in ten numeric ids of that length
  (snowflake ids, order numbers) passes the Luhn check, and replacing them would corrupt ordinary data. Send card
  numbers as strings, or keep them out of traced payloads. A card number glued to a word or an underscore
  (`card4242424242424242`) is not detected either (see "Card numbers in payloads" above).
- **Constraint validation in migrations sees every row.** Migrations run as the table owner, which row-level
  security also applies to (`FORCE ROW LEVEL SECURITY`), and Postgres validates a foreign key with a query that
  the policies filter. Each `VALIDATE CONSTRAINT` (migrations 0013, 0400, 0401 and 0505) now runs with the worker
  bypass set for its own transaction, so a dangling reference fails the upgrade instead of being marked valid. A
  CHECK constraint is validated by scanning the table, which the policies never filtered; those migrations take
  the same path so every validation follows one rule.
