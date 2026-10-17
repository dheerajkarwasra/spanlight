# Rotate secrets

## Purpose and when to use

Replace a secret because it leaked, because someone who knew it left, or on a schedule. This page covers the four application secrets:

| Secret | What it does | Impact of rotating |
|---|---|---|
| `SECRET_KEY` | Signs CSRF tokens, two-factor login challenges and OAuth state | Nobody is signed out; a few in-flight actions fail once |
| `METRICS_TOKEN` | Bearer token for `/metrics` | Your Prometheus must get the new value |
| `APP_DB_PASSWORD` | Password of the `spanlight_app` database role | A short restart of the api and worker |
| `CREDENTIALS_KEYS` | Master keys that encrypt stored two-factor seeds and gateway provider credentials | Two phases, then a re-seal; never drop an old key that still seals data |

Rotate one secret at a time, verify, then do the next. The database owner's password (`POSTGRES_PASSWORD`) and your Anthropic, S3, SMTP or OAuth credentials are changed with their providers and then in `deploy/.env`; see [Other credentials](#other-credentials).

## Prerequisites

- Write access to `deploy/.env` (Compose) or the Railway project.
- The old value of `CREDENTIALS_KEYS`, kept somewhere other than the database backups. If it is lost, see the warning in that section.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- A way to generate a random value. This is the one `deploy/.env.example` recommends:

  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

On Railway, set a variable without echoing it, as the [Railway guide](../deploy/railway.md) does:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set NAME --stdin --service api --skip-deploys
```

Then redeploy the service with `railway redeploy --service api`. A variable that is sealed in the Railway dashboard cannot be read back, so keep your own copy of what you set. Variables you set must also be declared in `deploy/railway/railway.ts`, otherwise the next `railway config apply` deletes them.

## SECRET_KEY

**What it protects.** `SECRET_KEY` is the HMAC key behind three things, and only these:

- the CSRF token that is bound to each session cookie;
- the signed token that carries a user between the password step and the two-factor code step of sign-in (valid 5 minutes);
- the signed state of a GitHub or Google sign-in in progress (valid 10 minutes).

Sessions themselves are random server-side tokens stored hashed in Postgres. They do not depend on `SECRET_KEY`, so rotating it does **not** sign anyone out.

**What rotation does.**

- Every existing CSRF cookie stops verifying. The first write a browser makes after the rotation fails with `403 CSRF_FAILED`; the dashboard calls `GET /api/v1/auth/me` on load, which reissues the cookie, so a page reload fixes it.
- A user who is between the password and the two-factor code, or in the middle of a provider sign-in, has to start again.
- API keys, personal access tokens, passwords and sealed two-factor seeds are not affected.

If the rotation is because a session may have been stolen, `SECRET_KEY` is not the lever. End the sessions instead: see [revoke-key.md](revoke-key.md#end-every-session-of-a-user).

### Steps (Docker Compose)

1. Generate a value and put it in `deploy/.env` as `SECRET_KEY=` (replace the old line). It must not be the built-in development value, and it is required when `APP_BASE_URL` is https, or the api refuses to start with `SECRET_KEY must be set when APP_BASE_URL uses https`.
2. Recreate the containers that use it:

   ```bash
   spl up -d
   ```

3. Verify. `spl ps` shows `api` and `worker` running for less than a minute, and:

   ```bash
   curl -fsS "$BASE/health/ready"
   ```

   Expected: `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`. Then sign in through the dashboard and create or edit something (a project name, for example). That exercises a CSRF-protected write with a freshly issued token.

### Steps (Railway)

`SECRET_KEY` is set on the `api` service only.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set SECRET_KEY --stdin --service api --skip-deploys
railway redeploy --service api
curl -fsS https://$DOMAIN/health/ready
```

**Roll back:** put the old value back and redeploy. Nothing was changed in the database.

## METRICS_TOKEN

**What it protects.** `/metrics` on the api and, when `WORKER_METRICS_PORT` is set, on the worker (neither is reachable through `web`). With no `METRICS_TOKEN` the endpoint answers `404`; with a wrong one it answers `401`.

### Steps (Docker Compose)

1. Generate a value, set `METRICS_TOKEN=` in `deploy/.env`, and recreate:

   ```bash
   spl up -d
   ```

2. Verify from inside the api and worker containers, which hold the new token in their environment (the image has no `curl`, but it has Python). The worker serves its own `/metrics` on port 9100 with the same token when `WORKER_METRICS_PORT` is set (the Compose file sets it):

   ```bash
   spl exec api python -c "import os, urllib.request as u; r = u.Request('http://127.0.0.1:8000/metrics', headers={'Authorization': 'Bearer ' + os.environ['METRICS_TOKEN']}); print(u.urlopen(r).status)"
   ```

   ```bash
   spl exec worker python -c "import os, urllib.request as u; r = u.Request('http://127.0.0.1:9100/metrics', headers={'Authorization': 'Bearer ' + os.environ['METRICS_TOKEN']}); print(u.urlopen(r).status)"
   ```

   Expected: `200` from each.
3. Give Prometheus the new token and reload it. Until you do, its scrapes of both the api and the worker get `401` and `up` is `0`, which is why [the SLO alerts](slo.md) wait several minutes before firing. With a file-based credential:

   ```yaml
   scrape_configs:
     - job_name: spanlight-api
       metrics_path: /metrics
       authorization:
         type: Bearer
         credentials_file: /etc/prometheus/spanlight-metrics-token
       static_configs:
         - targets: ["api:8000"]
   ```

   Write the new token to that file, then send Prometheus a reload (`kill -HUP <pid>`, or `POST /-/reload` if you started it with `--web.enable-lifecycle`).

### Steps (Railway)

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set METRICS_TOKEN --stdin --service api --skip-deploys
railway redeploy --service api
```

Update your scraper, then confirm a scrape returns 200. `railway.ts` declares `METRICS_TOKEN` on the api only; a worker that serves metrics needs the opt-in worker-metrics block ([Optional features](../deploy/railway.md#optional-features)), which adds it there.

**Roll back:** set the old value again and redeploy.

## APP_DB_PASSWORD

**What it protects.** The password of the `spanlight_app` role that the api and worker connect as. It is at least 16 characters (`spanlight ensure-app-role` refuses a shorter one). Use a URL-safe value such as the `token_urlsafe` output above: it is placed inside the `DATABASE_URL` connection string.

**How it changes.** `spanlight ensure-app-role` runs `ALTER ROLE spanlight_app ... PASSWORD <APP_DB_PASSWORD>` as the database owner. The `migrate` service does this on every `up`, so changing the variable and recreating the stack is the whole procedure. Running api and worker processes keep their open connections until they are recreated.

### Steps (Docker Compose)

1. Generate a value and set `APP_DB_PASSWORD=` in `deploy/.env`.
2. Recreate the stack. `migrate` runs first and sets the new password; `api` and `worker` then start with the new connection URL.

   ```bash
   spl up -d
   spl logs migrate
   ```

   Expected from `migrate`:

   ```
   Database is at the latest migration.
   Role spanlight_app is ready (no superuser, no RLS bypass).
   ```

3. Verify the role is still unprivileged and the api can connect with the new password:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -tA -c \
     "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'spanlight_app'"
   curl -fsS "$BASE/health/ready"
   ```

   Expected: `spanlight_app|f|f` and `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`.

If the api restarts in a loop with a password authentication error, `deploy/.env` and the database disagree. Set the database back to what `.env` says:

```bash
spl run --rm migrate spanlight ensure-app-role --role spanlight_app
spl restart api worker
```

### Steps (Railway)

`APP_DB_PASSWORD` is set on `api`; the worker's `DATABASE_URL` refers to `${{api.APP_DB_PASSWORD}}`. The api's pre-deploy command runs `ensure-app-role`, so deploying the api changes the password in the database. Use `railway up --service api`, which runs the pre-deploy command; do not rely on `railway redeploy` re-running it.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set APP_DB_PASSWORD --stdin --service api --skip-deploys
railway up --service api
railway redeploy --service worker
curl -fsS https://$DOMAIN/health/ready
```

Watch `railway logs --service api --latest` for `Role spanlight_app is ready (no superuser, no RLS bypass).` before you redeploy the worker. If that line is missing from the deploy logs, the password was not changed in the database: run `railway up --service api` again. The worker needs no pre-deploy step, only the new variable, so `railway redeploy --service worker` is enough for it. Between the two redeploys the worker still holds its old connections; if its restart policy cycles it before the redeploy, it recovers by itself once it has restarted with the new reference.

**Roll back:** set the old value, run `railway up --service api` (the role gets the old password back), then redeploy the worker.

## CREDENTIALS_KEYS

**What it protects.** The master keys that seal two-factor seeds and the LLM gateway's provider credentials (and other secrets the app must read back) before they reach the database. Format, from `backend/app/core/crypto.py`:

```
<key_id>:<base64 of 32 bytes>[,<key_id>:<base64 of 32 bytes>...]
```

A key id is 1 to 64 letters, digits, `.`, `_` or `-`. The base64 is the standard alphabet, with padding. **The first entry seals new data; every entry can open data sealed under its id.** That is what makes rotation possible, and it is why you keep the old entry. The api and the worker must hold the same value. A value that breaks the format stops the process at startup with a message such as `CREDENTIALS_KEYS is invalid: entry 1 ...` that names the entry and the rule, never the value.

**Back it up first.** A lost key cannot be recovered: every seed sealed under it becomes unreadable and those users have to enrol again, and every provider credential and billing source admin key sealed under it has to be entered again. Keep a copy of the whole value outside the database backups. See [the decision record](../decisions/0009-application-master-key-encryption.md).

Putting a new key first changes the key that seals **new** data; the old key stays for the old data. Provider credentials and billing source admin keys can be moved to the new key with `spanlight reseal-credentials` (step 6 below). Two-factor seeds have no such command: they move to the new key when the user enrols again, so a seed stays under its old key until then.

### Steps: add a new key (two phases)

Why two phases: the api seals and the worker opens. If the new key went first on one service while the other did not know it, a seed sealed under it could not be opened. So every service learns the new key before any service seals with it.

1. Generate a key with a new id:

   ```bash
   echo "v2:$(openssl rand -base64 32)"
   ```

   Save the output in your password manager now.

2. **Phase one: append the new key after the old one**, on every service that holds `CREDENTIALS_KEYS` (api and worker):

   ```
   CREDENTIALS_KEYS=v1:<old key>,v2:<new key>
   ```

   Compose: edit `deploy/.env` then `spl up -d`. Railway: set the variable on both services with `railway variable set CREDENTIALS_KEYS --stdin --service api --skip-deploys` and the same for `--service worker`, then `railway redeploy` each. New data is still sealed under `v1`.

3. Verify both services are up and ready:

   ```bash
   curl -fsS "$BASE/health/ready"
   spl logs --since 2m worker | grep worker_started
   ```

4. **Phase two: put the new key first:**

   ```
   CREDENTIALS_KEYS=v2:<new key>,v1:<old key>
   ```

   Apply it to both services as in step 2 (and to the standalone `gateway` service if you run one). From now on new two-factor enrolments and new or rotated provider credentials are sealed under `v2`; existing ones still open under `v1`.

5. Verify. Sign in with an account that has two-factor on (this opens a seed sealed under `v1`), and enrol a second test account (this seals under `v2`). Then see which key each seed uses:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT totp_key_id, count(*) FROM users WHERE totp_key_id IS NOT NULL GROUP BY 1"
   ```

6. Re-seal the gateway's provider credentials and the billing sources' admin keys under the new first key:

   ```bash
   spl exec worker spanlight reseal-credentials
   ```

   Expected: `Re-sealed <n> provider credentials and <m> billing source keys.` and exit status 0. A billing source uploaded as CSV holds no key and is skipped. The command counts both tables again afterwards. It exits non-zero with `<n> provider credentials or billing sources are still sealed under an older key` while any row is left, and prints `<n> provider credentials or billing sources could not be opened with any key in CREDENTIALS_KEYS` on standard error for a row no listed key opens; its id is in the log. Keep every key in the list and run it again; a credential or billing source key that stays unopenable has to be entered again in the dashboard. See [the gateway runbook](gateway.md).

### Steps: retire an old key

Only when no row is sealed under it. Check every kind of sealed data:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT 'user' AS kind, count(*) FROM users WHERE totp_key_id = 'v1'
   UNION ALL
   SELECT 'provider_credential', count(*) FROM provider_credentials WHERE key_id = 'v1'
   UNION ALL
   SELECT 'billing_source', count(*) FROM billing_sources WHERE key_id = 'v1'"
```

Every count must be `0`. If the provider credential or billing source count is not, run `spl exec worker spanlight reseal-credentials` (step 6 above) until it exits 0, and run the query again. **Stop here if any count is still above zero:** do not remove the key. A billing source still under a removed key fails every fetch with `key cannot be decrypted` until its admin key is entered again.

If the key leaked and you must retire it with seeds still under it, turn two-factor off for those users so they enrol again under the new key. List them:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT email FROM users WHERE totp_key_id = 'v1'"
spl exec worker spanlight reset-2fa --email ada@example.com
```

Expected: `Two-factor authentication turned off for <ada@example.com>; recovery codes deleted.` Repeat for each user in the list, then confirm the check above shows `0` for every kind. Only then remove `v1:<old key>` from `CREDENTIALS_KEYS` on every service (api, worker and a standalone gateway) and redeploy. If you remove a key too early, a user whose seed is sealed under it cannot complete sign-in (the api fails to open the seed with an unknown-key error), a gateway route that uses a credential sealed under it fails its calls, and a billing source whose admin key is sealed under it fails every fetch with `key cannot be decrypted`. Put the key back to recover.

**Roll back:** put the previous value back on both services. As long as the old key is still in the list, nothing sealed is lost. Data sealed under `v2` in the meantime opens only while `v2` stays in the keyring, so do not drop `v2` once a user has enrolled under it.

## Other credentials

- **`POSTGRES_PASSWORD`** (Compose). Postgres reads it only when it creates an empty data directory, so editing `deploy/.env` alone changes nothing in the database and breaks `migrate`. Change it in the database first, with a prompt that keeps it out of your shell history, then in `.env` (and in `BACKUP_DATABASE_URL` if that URL embeds it), then recreate. Note that `up -d` recreates the postgres container when its environment changed, which restarts the database: expect a brief outage.

  ```bash
  spl exec postgres psql -U postgres -d spanlight -c '\password postgres'
  # edit deploy/.env: POSTGRES_PASSWORD=..., BACKUP_DATABASE_URL=...
  spl up -d
  ```

  On Railway the owner credentials come from the Postgres service itself; rotate them there.
- **`ANTHROPIC_API_KEY`, `S3_ACCESS_KEY` / `S3_SECRET_KEY`, `SMTP_PASSWORD`, `RESEND_API_KEY`, `OAUTH_*_CLIENT_SECRET`, `SENTRY_DSN`.** Create the new credential with the provider, set it in `deploy/.env` (or with `railway variable set`), recreate or redeploy, then revoke the old one at the provider. The app reads them at startup, so a restart is required.

## Verification

After any rotation:

- `curl -fsS "$BASE/health/ready"` returns `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`.
- `spl logs --since 5m api worker` has no startup validation errors (`SECRET_KEY must be set...`, `CREDENTIALS_KEYS is invalid...`) and no repeated password-authentication failures.
- Signing in and a write in the dashboard work.
- The old value no longer appears in `deploy/.env`, your shell history or any chat or ticket where you pasted it.
