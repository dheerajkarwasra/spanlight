# Deploy to Railway

This runs the public demo on Railway's Hobby plan: Railway Postgres plus three services built from [`deploy/Dockerfile`](../../deploy/Dockerfile).

| Service | What it runs | Public |
|---|---|---|
| `postgres` | Railway's managed Postgres | no |
| `api` | FastAPI on port 8000. Before each deploy it runs migrations as the Postgres owner and refreshes the `spanlight_app` role. | no |
| `worker` | The same image, `python -m app.jobs.worker` | no |
| `web` | Caddy on port 8080: the SPA, plus `/api`, `/v1` and `/health` proxied to `api` over the private network | yes |

The whole setup lives in [`deploy/railway/railway.ts`](../../deploy/railway/railway.ts), a Railway [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code) file. Railway's older `railway.toml` / `railway.json` (Config as Code) is deprecated and new services can't use it.

Expect about **$5–6 a month** on Hobby (see [Cost](#cost)), plus at most $1 of Anthropic usage for the live demo.

## How it fits together

- **One Dockerfile, two images.** Railway can't pass `docker build --target`. Each service sets the `SPANLIGHT_TARGET` variable (`backend` or `web`), which Railway passes as a build argument, and the last stage of `deploy/Dockerfile` builds from that target.
- **Row-level security holds.** The api and worker connect as `spanlight_app`, which is not a superuser and cannot bypass RLS. Only the api's pre-deploy command uses the Postgres owner, through `MIGRATION_DATABASE_URL`.
- **Real client IPs.** Railway's edge sets `X-Real-IP`. With `TRUSTED_PROXIES` set on `web`, Caddy passes that address to the api as `X-Forwarded-For`, so login throttling, demo rate limits and the sessions list see each visitor's own IP.
- **Secrets stay out of git.** You set them with `railway variable set --stdin`. `railway.ts` declares them as `preserve()`, so applying the file keeps them.

## Pre-deploy checklist

Checked against the code on 2026-10-08. A ticked item was verified in the repository or by a local run, and says where. An unticked item needs the owner's live Railway project and says what to look at.

Why the database items matter: a Postgres superuser bypasses row-level security even when it is forced on a table. If the api or worker connected as the Postgres owner, every tenant's data would be readable by every other tenant and nothing would fail. So the pre-deploy step migrates as the owner, and the running services connect as `spanlight_app`.

**Image and services**

- [x] One Dockerfile builds both images from `SPANLIGHT_TARGET`. `deploy/Dockerfile` declares `ARG SPANLIGHT_TARGET=web` and ends in `FROM ${SPANLIGHT_TARGET}`. In `railway.ts`, `api` and `worker` set `SPANLIGHT_TARGET` to `backend` and `web` sets it to `web`. All three builds succeeded: `--build-arg SPANLIGHT_TARGET=backend`, `--build-arg SPANLIGHT_TARGET=web`, and no argument, which gives `web`. The backend image runs `spanlight --help`; the other two run `caddy version`.
- [x] The worker runs `python -m app.jobs.worker`: `worker` → `start` in `railway.ts`. It has no healthcheck and no public domain.
- [x] The pre-deploy command is on `api` only: `api` → `preDeploy` in `railway.ts`. Run in the backend image against a scratch Postgres, it printed `Database is at the latest migration.` and `Role spanlight_app is ready (no superuser, no RLS bypass).`.

**Database roles**

- [x] `MIGRATION_DATABASE_URL` is the Postgres owner's URL (`postgres.PGUSER`), set on `api` only. `railway.ts` doesn't set it on `worker` or `web`.
- [x] Only the pre-deploy step uses it. `preDeploy` copies it into `DATABASE_URL` for that one command. The image's `CMD` runs `unset MIGRATION_DATABASE_URL` before `exec uvicorn`. With the variable set on the container, `/proc/1/environ` of the running uvicorn didn't contain it.
- [x] `APP_DB_PASSWORD` is set on `api` (`preserve()`), the service whose pre-deploy runs `spanlight ensure-app-role`. The worker only references it as `${{api.APP_DB_PASSWORD}}` inside its `DATABASE_URL`.
- [x] `DATABASE_URL` connects as `spanlight_app` on `api` and on `worker`. As that role, `pg_roles` shows `rolsuper` and `rolbypassrls` both false, and the api image answered `/health/ready` with `{"status":"ok","database":"ok","migrations":"ok"}` (that was before the response gained `worker_heartbeat_age_s` and `outbox_backlog`; see step 6.1 for the current body).

**Variables**

- [x] `APP_BASE_URL` on `api` is `https://${{web.RAILWAY_PUBLIC_DOMAIN}}`, the public https origin. Generate the domain before the first api deploy (step 4).
- [x] `API_UPSTREAM` on `web` is `${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}`, the api's private host and port. `deploy/Caddyfile` reads it as `{$API_UPSTREAM}`. `GATEWAY_UPSTREAM` has the same value and is read as `{$GATEWAY_UPSTREAM}` for `/gw/*`; leave it out and every gateway call answers 502.
- [x] `TRUSTED_PROXIES` on `web` is `0.0.0.0/0 ::/0`, which `deploy/Caddyfile` reads as `{$TRUSTED_PROXIES}`.
- [x] `SECRET_KEY` (api), `METRICS_TOKEN` (api), `ANTHROPIC_API_KEY` (worker), and `SENTRY_DSN` and `CREDENTIALS_KEYS` (api and worker) are declared with `preserve()`, so `railway config apply` keeps the values you set. A variable that is set in Railway but not declared in `railway.ts` is deleted by the next apply, so every variable you set must be in the file. The optional features are the same, once you uncomment their blocks (see [Optional features](#optional-features)).
- [x] `DEMO_MONTHLY_BUDGET_USD` on `worker` is `1.00`.
- [x] `railway.ts` is valid. It type-checks under strict TypeScript with the pinned SDK (`railway` 3.13.0), and evaluating it gives the variables above.

**Needs the live Railway project (owner)**

- [ ] `railway config apply` works against your project: the first apply may stop on the `preserve()` secrets, which can't exist before the services do, so follow the procedure in step 2; after step 3, `railway config plan` should show no changes. The CLI needs your login, so none of this was run.
- [ ] The api's first pre-deploy succeeds on Railway: the deploy logs show the two lines above (step 5).
- [ ] `curl -fsS https://$DOMAIN/health/ready` returns `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}` (the age is a few seconds, never `null`) and the landing page loads (step 6.1).
- [ ] The worker ran the `demo_traffic` job and **Try the live demo** opens a session with real traces (step 6.5).
- [ ] Sentry and the uptime check are set up ([Monitoring](#monitoring)).
- [ ] A "Deploy on Railway" template button. Publishing a template needs your Railway account (workspace settings → Templates), so there is no button in the README yet. Add it once the template exists.

## Before you start

- A Railway account on the Hobby plan.
- The Railway CLI, version 5.42.1 or newer, and Node 22 or newer (the CLI uses Node to evaluate `railway.ts`):

  ```bash
  brew install railway
  railway --version
  node --version
  ```

- Sign in:

  ```bash
  railway login
  ```

- A [Sentry](https://sentry.io) account (a free plan is enough) and an [Anthropic API key](https://console.anthropic.com), both of which step 3 needs. This runbook requires `SENTRY_DSN` for the live demo at spanlight.dev. Self-hosting with Docker Compose is different: leave `SENTRY_DSN` blank in `deploy/.env` and error reporting stays off. On Railway with this file, set it, or delete its two `preserve()` lines from `railway.ts` first. `CREDENTIALS_KEYS`, the key that encrypts stored secrets such as two-factor seeds, works the same way: left blank, two-factor authentication stays "not configured", and on Railway you delete its two `preserve()` lines from `railway.ts` to leave it out. Step 3 generates it, and its warning about backups applies before you run it.

Run every command below from the repository root.

## 1. Create the project

```bash
railway init --name spanlight
```

This creates an empty project with a `production` environment and links this directory to it.

## 2. Create Postgres and the services

```bash
npm install --prefix deploy/railway
railway config plan  --file deploy/railway/railway.ts
railway config apply --file deploy/railway/railway.ts
```

The plan should add `database postgres` and the services `api`, `worker` and `web`, with no changes to anything else. Postgres starts on its own. The three services stay empty until you deploy code to them in step 5.

Six variables are declared with `preserve()` because you set their values yourself in step 3: `SECRET_KEY`, `APP_DB_PASSWORD`, `METRICS_TOKEN`, `SENTRY_DSN`, `ANTHROPIC_API_KEY` and `CREDENTIALS_KEYS`. None of them can exist before the services do, so this first apply may stop on them. If it does, comment out the `preserve()` lines in `railway.ts`, apply, do step 3, restore the lines and run `railway config plan` again, which should show no changes (see [Troubleshooting](#troubleshooting)). Once step 3 is done all six exist, so every later apply is safe.

The optional settings of later features (object storage, backups, self-tracing, worker metrics, `WORKER_REQUIRED`) are not live in `railway.ts`: they are commented-out opt-in blocks, so the first apply does not wait for them. See [Optional features](#optional-features).

## 3. Set the secrets

Create the Sentry project first ([Monitoring](#sentry-errors), item 1). It takes two minutes and gives you the DSN used below.

Run this block once. Running it again replaces every secret it sets. Replacing `CREDENTIALS_KEYS` with a freshly generated key makes everything already sealed under the old one unreadable (two-factor seeds, for example), and replacing `SECRET_KEY` invalidates in-flight CSRF tokens, login challenges and OAuth state (sessions survive; see [Secret rotation](../runbooks/rotate-secrets.md)). To change one value later, set only that variable with `railway variable set NAME --stdin --service <service>`, and rotate `CREDENTIALS_KEYS` only as described in [Secret rotation](../runbooks/rotate-secrets.md).

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set SECRET_KEY --stdin --service api --skip-deploys
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set APP_DB_PASSWORD --stdin --service api --skip-deploys

# Copy your Anthropic API key to the clipboard first.
pbpaste | tr -d '[:space:]' \
  | railway variable set ANTHROPIC_API_KEY --stdin --service worker --skip-deploys

python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
  | railway variable set METRICS_TOKEN --stdin --service api --skip-deploys

# Now copy the DSN of your Sentry project to the clipboard. It starts with https:// and the
# clipboard still holds the Anthropic key until you do, so this refuses to send anything else.
dsn=$(pbpaste | tr -d '[:space:]')
case $dsn in
  https://*)
    for svc in api worker; do
      printf %s "$dsn" | railway variable set SENTRY_DSN --stdin --service $svc --skip-deploys
    done ;;
  *) echo "The clipboard doesn't hold a Sentry DSN (it should start with https://)" >&2 ;;
esac
unset dsn

# One key for both services: the api seals secrets and the worker opens them, so they must hold
# the same value. The id before the colon names the key; rotation relies on it (see below).
keys="v1:$(openssl rand -base64 32)"
for svc in api worker; do
  printf %s "$keys" | railway variable set CREDENTIALS_KEYS --stdin --service $svc --skip-deploys
done
# Copy it, then paste it into your password manager before you do anything else.
printf %s "$keys" | pbcopy
unset keys

# Once the key is safe in your password manager, empty the clipboard.
pbcopy </dev/null
```

None of these values appears on screen or in your shell history. The commands strip trailing newlines because `APP_DB_PASSWORD` ends up inside a connection URL. To hide the secrets in the dashboard too, seal them: open the service's **Variables** tab, then **⋮ → Seal**. Sealing is permanent, and Railway doesn't return sealed values to `railway run` or `railway variable list`. So seal `SECRET_KEY`, `METRICS_TOKEN` and `ANTHROPIC_API_KEY` now, `CREDENTIALS_KEYS` only once its copy is safe in your password manager (after sealing, nobody can read it back), and `SENTRY_DSN` only after the [Monitoring](#sentry-errors) checks, which need it. Seal `APP_DB_PASSWORD` last: Railway's docs don't say whether references to a sealed variable still resolve, and the database URLs refer to this one. After sealing it, redeploy the api and check that its pre-deploy still succeeds.

All variables, for reference. Values in `${{ }}` are Railway [reference variables](https://docs.railway.com/variables#reference-variables), resolved when a service deploys.

| Variable | Service | Value | Secret |
|---|---|---|---|
| `SPANLIGHT_TARGET` | api, worker | `backend` | no |
| `SPANLIGHT_TARGET` | web | `web` | no |
| `PORT` | api | `8000` | no |
| `PORT` | web | `8080` | no |
| `APP_BASE_URL` | api | `https://${{web.RAILWAY_PUBLIC_DOMAIN}}` | no |
| `DATABASE_URL` | api | `postgresql+psycopg://spanlight_app:${{APP_DB_PASSWORD}}@${{postgres.PGHOST}}:${{postgres.PGPORT}}/${{postgres.PGDATABASE}}` | holds one |
| `MIGRATION_DATABASE_URL` | api | `postgresql+psycopg://${{postgres.PGUSER}}:${{postgres.PGPASSWORD}}@…` (same host, port and database) | holds one |
| `DEMO_ENABLED` | api | `true` | no |
| `SECRET_KEY` | api | random, step 3 | yes |
| `APP_DB_PASSWORD` | api | random, at least 16 characters, step 3 | yes |
| `METRICS_TOKEN` | api | random, step 3 | yes |
| `GATEWAY_ORG_RPM_CEILING` | api | `60`: the most gateway requests per minute one organization may send across its keys (the LLM gateway runs inside the api; see the [gateway runbook](../runbooks/gateway.md)) | no |
| `DATABASE_URL` | worker | as on api, with `${{api.APP_DB_PASSWORD}}` | holds one |
| `DEMO_MONTHLY_BUDGET_USD` | worker | `1.00` | no |
| `ANTHROPIC_API_KEY` | worker | your key, step 3 | yes |
| `SENTRY_DSN` | api, worker | your Sentry project's DSN, step 3 | yes |
| `WORKER_REQUIRED` | api | opt-in (see [Optional features](#optional-features)): `false` stops `/health/ready` answering 503 while no worker is beating, for an api-only setup; empty keeps the default, `true` | no |
| `CREDENTIALS_KEYS` | api, worker | `v1:` followed by `openssl rand -base64 32`, the same value on both, step 3 | yes |
| `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | api, worker | opt-in (see [Optional features](#optional-features)): an S3-compatible bucket and its keys; turn on backups and exports. Set all three or none, with the same values on both services | access key and secret: yes |
| `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT`, `S3_REGION`, `S3_FORCE_PATH_STYLE` | api, worker | opt-in (see [Optional features](#optional-features)): object-storage address for non-AWS services, the address browsers use for download links, the AWS region, and path-style bucket addressing (MinIO and most self-hosted stores) | no |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | api, worker | opt-in (see [Optional features](#optional-features)): OTLP HTTP collector that receives the app's own traces; self-tracing is off while empty | no |
| `OTEL_SERVICE_NAME` | api, worker | opt-in (see [Optional features](#optional-features)): service name on those traces; empty keeps `spanlight-api` | no |
| `BACKUPS_ENABLED` | worker | opt-in (see [Optional features](#optional-features)): `false` turns the nightly backup job off; empty keeps it on (it still needs the object storage and `BACKUP_DATABASE_URL`) | no |
| `BACKUP_DATABASE_URL` | worker | opt-in (see [Optional features](#optional-features)): connection URL of a role that bypasses row-level security (the Postgres owner), used by the nightly `pg_dump`; the job ends `skipped_not_configured` without it | yes |
| `WORKER_METRICS_PORT` | worker | opt-in (see [Optional features](#optional-features)): port for the worker's own `/metrics`. Leave empty unless a scraper runs in the project; it also needs `METRICS_TOKEN` on the worker, which is in the same opt-in block | no |
| `API_UPSTREAM` | web | `${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` | no |
| `GATEWAY_UPSTREAM` | web | `${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}`, where Caddy sends `/gw/*` (the LLM gateway, embedded in the api) | no |
| `TRUSTED_PROXIES` | web | `0.0.0.0/0 ::/0` | no |

### Optional features

These settings are off by default and, because `preserve()` makes an apply stop on a variable that has no value, they are commented out in `deploy/railway/railway.ts` in blocks grouped by feature. To enable one: set its variables with `railway variable set NAME --stdin --service <service> --skip-deploys` (the services are named in the block), uncomment the block in `railway.ts` on each service, run `railway config plan` and `railway config apply`, then redeploy the services. Set the variables before you uncomment, or the apply stops on the missing value. To disable a feature again, comment the block out and remove the variables with `railway variable delete`, or the next apply deletes them anyway.

| Feature | Block, on which services | Variables |
|---|---|---|
| Object storage (exports and backups) | api and worker together, the same values on both | `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (all three or none), plus `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT`, `S3_REGION`, `S3_FORCE_PATH_STYLE` as your store needs |
| Nightly backups | worker | `BACKUPS_ENABLED`, `BACKUP_DATABASE_URL` |
| Self-tracing | api and worker | `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` |
| Worker metrics | worker | `METRICS_TOKEN` (the api's value) and `WORKER_METRICS_PORT` |
| Api without a worker | api | `WORKER_REQUIRED` set to `false` |

`METRICS_TOKEN` guards `/metrics` on the api. Caddy doesn't proxy that path, so only something on the private network can scrape it, and without a token it answers 404. Set the token even if nothing scrapes yet: every `preserve()` variable has to exist before you apply `railway.ts` again.

The worker's job metrics (billing-source fetches, provider status polls, attribution refreshes, retention and the other scheduled jobs) are served only on the worker's own `/metrics`, not the api's. Until the worker metrics block is enabled and a scraper runs in the project, none of them is collected, so a failing job shows only in the worker's logs and in Sentry.

`CREDENTIALS_KEYS` holds the master key that encrypts secrets the app must read back, such as two-factor seeds, before they reach the database. Its format is `<key id>:<base64 of 32 bytes>`, and the first entry encrypts new data. Three things to know:

- **Set the same value on the api and the worker.** The step 3 command does this in one loop. If the two differ, one service can't open what the other sealed.
- **Back it up offline, apart from the database backups.** Nothing can recover a lost key: every secret sealed under it becomes unreadable, and the users concerned have to enrol again. A copy stored next to the database protects nothing.
- **Leave it unset and nothing is stored unencrypted.** Two-factor authentication answers "not configured" instead. On Railway, delete the two `preserve()` lines from `railway.ts` to run without it.

To rotate it, put a new entry first and keep the old one (`v2:…,v1:…`) on both services. Add the new entry after the old one and deploy first, so neither service seals under a key the other doesn't know yet, then move it to the front. The reasoning, and why you must not drop a key that still seals data, is in [the decision record](../decisions/0009-application-master-key-encryption.md).

To add a variable later, set it with the CLI **and** add it to `railway.ts` (as `preserve()` for a secret). Otherwise the next `railway config apply` deletes it.

## 4. Generate the public domain

```bash
railway domain --service web --port 8080
export DOMAIN=<the-name>.up.railway.app   # from the output above
```

Only `web` gets a domain. The api's `APP_BASE_URL` points at this domain, so generate it before the api's first deploy.

### Custom domain

To serve Spanlight on your own domain instead of the generated `*.up.railway.app` name, do this now or at any time after the first deploy.

1. **Add the domain to `web`.** In the dashboard, open **web → Settings → Networking → Custom Domain**, enter the domain and target port `8080`. Railway shows two DNS records: a CNAME pointing at `<id>.up.railway.app`, and a TXT record at `_railway-verify.<your domain>`.
2. **Create both records at your DNS provider.** A bare domain (`example.com` rather than `app.example.com`) needs a provider that supports CNAME flattening or ALIAS records; Cloudflare does. Railway verifies the domain and issues a Let's Encrypt certificate, usually within a few minutes.
3. **On Cloudflare, set the record to DNS only (grey cloud).** With the proxy on, Railway's edge sees a Cloudflare address, so `X-Real-IP`, and with it login throttling, rate limits and the sessions list, carries Cloudflare's IP instead of each visitor's. Cloudflare also ends proxied requests after 100 seconds, which cuts off long gateway streams.
4. **Point the app at the new origin.** Set `APP_BASE_URL=https://<your domain>` on every service that sets it, and change the same line in `railway.ts` so the next apply doesn't revert it. Railway stages variable changes, so deploy them, then redeploy `api` and `worker`. From then on the origin check accepts only the new domain: sign-ups and logins on the old `*.up.railway.app` address get 403 `ORIGIN_NOT_ALLOWED`. To keep both working during a move, add the old origin to `ALLOWED_ORIGINS` on the api.
5. **Move the sign-in callbacks**, if GitHub or Google sign-in is configured. Register `https://<your domain>/api/v1/auth/oauth/github/callback` and `https://<your domain>/api/v1/auth/oauth/google/callback` with the providers. A GitHub OAuth app holds a single callback URL, so the old one stops working once you save. A Google OAuth client can list several, so add the new one first and remove the old one after sign-in works; also add the domain to the consent screen's authorized domains.
6. **Verify** with `export DOMAIN=<your domain>` and the checks in [step 6](#6-verify). A resolver that looked the name up before the records existed keeps answering "not found" for as long as the zone's negative-cache TTL (30 minutes on Cloudflare). Wait it out, or purge the name from public resolvers' caches.

## 5. Deploy

Deploy the api first. Its pre-deploy command creates the schema and the `spanlight_app` role that the worker connects as.

```bash
railway up --service api
railway up --service worker
railway up --service web
```

Each command uploads this directory (about 7 MB; `.gitignore` is respected), builds it on Railway and streams the logs. It exits 0 once the deployment is live. The repository doesn't need to be on GitHub.

In the api's deploy logs, the pre-deploy step should print `Database is at the latest migration.` and `Role spanlight_app is ready (no superuser, no RLS bypass).` The deploy goes live only once `/health/live` returns 200 (the api healthcheck is liveness only; `/health/ready` also needs the worker, which deploys after the api). If a deploy fails, `railway logs --service api --latest` shows its logs.

## 6. Verify

1. **Readiness**

   ```bash
   curl -fsS https://$DOMAIN/health/ready
   # {"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}
   ```

   `-f` makes curl exit with an error on a 503, which is what the api answers when the database is down, migrations are pending, no worker has reported in the last 120 seconds, or more than 1 000 notifications are overdue by over 10 minutes. `worker_heartbeat_age_s` is how many seconds ago the newest worker reported. `outbox_backlog` counts pending notifications and stops at 10 000. The landing page at `https://$DOMAIN` should load too.

2. **Sign up and onboarding.** Open `https://$DOMAIN`, create an account, and follow the setup wizard to create a project and an API key. A 403 here means `APP_BASE_URL` is wrong (see [Troubleshooting](#troubleshooting)).

3. **Client IPs.** This checks that the sessions list records your own address and that a forged `X-Real-IP` is ignored. Use a test account; the password goes into your shell history.

   ```bash
   curl -s -c /tmp/spanlight.cookies -H "Origin: https://$DOMAIN" -H 'Content-Type: application/json' \
     -H 'X-Real-IP: 203.0.113.7' \
     -d '{"email":"you@example.com","password":"your-test-password"}' \
     https://$DOMAIN/api/v1/auth/login > /dev/null
   curl -s -b /tmp/spanlight.cookies https://$DOMAIN/api/v1/auth/sessions; rm /tmp/spanlight.cookies
   ```

   Every session's `ip` should be your public IP. If you see `203.0.113.7`, the edge forwarded the forged header: remove `TRUSTED_PROXIES` from `web` and redeploy it. If you see a private address (`10.…`, `100.…`, `fd…`), Caddy isn't trusting the edge (see [Troubleshooting](#troubleshooting)).

4. **Send a trace with the SDK.** This uses the API key from step 2 and makes one real, sub-cent Claude call.

   ```bash
   cd sdks/python
   export SPANLIGHT_HOST=https://$DOMAIN SPANLIGHT_API_KEY=spl_live_… ANTHROPIC_API_KEY=sk-ant-…
   uv run --extra anthropic python examples/support_bot.py "How do refunds work?"
   ```

   The trace shows up in the wizard and the trace explorer within a few seconds.

5. **Live demo.** The worker adds real demo traffic every 30 minutes, starting right after it boots.

   ```bash
   railway logs --service worker --lines 500 | grep demo_traffic
   ```

   Expect a `demo_traffic_recorded` line (it carries the new `trace_id`) followed by a `job_done` line with `"kind": "demo_traffic"`. A `demo_traffic_skipped` line says why nothing ran: no `ANTHROPIC_API_KEY`, or this month's $1 budget is spent. If the first lines say nothing yet, wait a minute and run it again.

   Then sign out, open `https://$DOMAIN` and choose **Try the live demo** on the landing page. It should open the dashboard in the read-only `Demo` workspace, with the support-bot traces the worker just recorded. In a private window, the button should work without any account.

## Monitoring

Two things watch the demo: Sentry reports errors, and an external uptime monitor checks that the site answers. Create the Sentry project before deploy step 3 so its DSN is set with the other secrets. Do the checks and the uptime monitor after the first successful deploy.

### Sentry (errors)

1. **Create the project** (before deploy step 3). Sign in at [sentry.io](https://sentry.io) (the free Developer plan is enough), then **Projects → Create Project**. Pick the Python / FastAPI platform, name it `spanlight`, and leave the default alert rule on, which emails you about new issues. Copy the DSN from the next screen, or later from **Settings → Projects → spanlight → Client Keys (DSN)**. Step 3 sets it on `api` and `worker` with `railway variable set SENTRY_DSN --stdin`, the same way as the other secrets.

2. **Check the DSN is on both services** (after the deploy, and before you seal it: sealed variables aren't listed by the CLI, so look in the dashboard instead). These print only the variable name, never the value, and each should print `SENTRY_DSN`:

   ```bash
   railway variable list --service api    --kv | cut -d= -f1 | grep -x SENTRY_DSN
   railway variable list --service worker --kv | cut -d= -f1 | grep -x SENTRY_DSN
   ```

3. **A forced event must arrive.** Run this first: if it works, the check in item 4 can't pass just because nothing could be sent. It sends one message from your own machine, using the api's variables. It needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and runs `railway run`, which executes a command locally with the service's variables injected. A sealed `SENTRY_DSN` is not injected, so do this check before sealing it.

   ```bash
   railway run --service api uv run --directory backend python -c '
   import os
   import sentry_sdk
   sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], send_default_pii=False)
   sentry_sdk.capture_message("Spanlight monitoring check")
   sentry_sdk.flush()
   print("sent")
   '
   ```

   It prints `sent`. Within a minute, the project's **Issues** page shows `Spanlight monitoring check` (level info). A `KeyError: 'SENTRY_DSN'` means the variable isn't on the api (step 3). If the event never arrives, the DSN is wrong: compare it with Sentry's Client Keys. Mark the issue resolved so it doesn't clutter the list.

4. **A client error must not create an event.** A request for a page that doesn't exist is a 404 the api answers correctly. The Sentry SDK only reports 5xx responses and unhandled exceptions ([`tests/core/test_sentry.py`](../../backend/tests/core/test_sentry.py) pins this).

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/api/v1/this-route-does-not-exist
   # 404
   ```

   Wait a minute, then refresh **Issues**. Nothing about this request should appear. If an issue does appear, 4xx filtering has regressed. Open an issue on the repository, because the alerts will be noisy until it's fixed.

What reaches Sentry, once the DSN is set:

- **api:** unhandled exceptions and any 5xx response, plus error-level log lines. 4xx responses (401, 403, 404, 422, 429) are never reported.
- **worker:** error-level log lines, so a failed job (`job_failed`) arrives as an error event whose message holds the log fields, and a crash while starting up arrives as an exception.
- **Not sent:** request bodies, the local variables of stack frames (FastAPI holds the parsed body in one), query strings, cookies and the `Referer` header. Sensitive request headers (`Authorization`, `X-Real-IP`, `X-Forwarded-For`) are replaced with `[Filtered]`, no user or client IP is attached, and the database engine hides SQL parameters, so a failed `INSERT` doesn't put its values in the error text. This is why a failed `POST /v1/traces` or sign-up should not carry a customer's prompts, completions or email address, and why an invite token in `/api/v1/invites/preview?token=` or in the `/invite/<token>` page URL should not either. The SDK's own default would send most of this, so the code sets `max_request_body_size="never"`, `include_local_variables=False`, `send_default_pii=False`, a `before_send` hook that drops the query string and `Referer`, and `hide_parameters=True` on the engine. The cost is that stack traces show no variable values. [`tests/core/test_sentry.py`](../../backend/tests/core/test_sentry.py) and [`tests/db/test_db_session.py`](../../backend/tests/db/test_db_session.py) pin each of these.
- **Still sent:** exception messages and the traceback's source lines (nothing scrubs them, so a message that includes a request value is sent as written), the request URL path, other request headers (host, user agent, origin) and the server's hostname. A failed worker job arrives with its `error` text, which is the exception type and message. Treat the Sentry project as holding operational data, and keep customer content out of exception messages.

### Uptime check

Use any free uptime monitor (for example UptimeRobot or Better Stack). Create one HTTP(S) monitor:

| Setting | Value |
|---|---|
| URL | `https://<your domain>/health/ready` |
| Method | `GET` |
| Interval | 5 minutes |
| Expected | HTTP 200 (optionally also the text `"status":"ok"`) |
| Alerts | email to the owner's address, on down and on recovery |

The path goes through `web` to the api, and the api answers 200 only when Postgres responds, migrations are at head, a worker has reported in the last 120 seconds and the notification outbox is draining. So it covers the proxy, the api, the database and the worker in one request, and returns something other than 200 if any of them is down: 503 from the api when the database is unreachable, migrations are pending, the worker is silent or mail is stuck, and an error from `web` when the api itself is down. Most monitors have a "send test alert" button: use it once to confirm the email arrives.

When it's set up, replace the placeholder below with the monitor's dashboard or public status page URL.

`Uptime dashboard: <owner fills in after setup>`

## Cost

Hobby costs $5 a month, and that includes $5 of usage. Rough usage for this project with light traffic:

| Item | Estimate | Per month |
|---|---|---|
| api memory | ~150 MB | ~$1.50 |
| worker memory | ~120 MB | ~$1.20 |
| web memory (Caddy) | ~20 MB | ~$0.20 |
| Postgres memory and volume | ~100 MB, under 1 GB of disk | ~$1.10 |
| CPU, all services | mostly the worker's 2-second poll | ~$0.30–0.60 |
| Egress | well under 1 GB | ~$0.05 |
| **Usage** | | **~$4.50–6** |

So the bill is about $5–6 a month. Builds are free. Prices: RAM $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB, volume $0.15/GB-month ([Railway pricing](https://docs.railway.com/pricing/plans)). After a few days, check the real numbers with:

```bash
railway usage projects --project spanlight
```

### Keep it low

- **Usage limit.** This sets an email alert at $8 and a hard stop at $15. The limit covers the whole workspace, and at the hard limit Railway takes every workload offline.

  ```bash
  railway usage limit set --target workspace --soft 8 --hard 15
  ```

- **Replica limits.** For each service, open **Settings → Deploy → Replica Limits** and cap it: api 1 vCPU / 1 GB, worker 1 vCPU / 512 MB, web 1 vCPU / 256 MB. You pay for what's used, not the cap; the cap stops a runaway process. Afterwards, run `railway config plan --file deploy/railway/railway.ts` and check it doesn't try to remove the limits.
- **Leave Serverless off.** The worker polls Postgres every 2 seconds, which keeps both it and Postgres awake. Sleeping `web` or `api` would save cents, and the first visitor after a sleep gets a slow load or a 502.
- **Keep Postgres private.** It has no public TCP proxy by default. Turning one on adds egress charges.
- **Anthropic.** `DEMO_MONTHLY_BUDGET_USD=1.00` stops demo traffic once the month's recorded cost reaches $1. Also set a monthly spend limit on the key in the Anthropic Console.

## Scaling

Replicas are the knob. Each api replica runs one process, and rate limits, sessions and idempotency keys live in Postgres, so replicas share them; the worker scales the same way (jobs are claimed with `FOR UPDATE SKIP LOCKED`). Raise a service's replica count in its **Settings → Deploy**, then check three things:

- **The connection budget.** An api replica opens up to 30 Postgres connections and a worker up to 4: `30 × api replicas + 4 × workers + 10 ≤ max_connections − 3`. Read `SHOW max_connections` on the Railway Postgres; at 100 that is two api replicas and one worker. The [scaling runbook](../runbooks/scale.md#2-the-connection-budget) has the details.
- **That every replica gets traffic.** `web` reaches the api through one private hostname. After scaling, each replica's deploy logs should show `http_request` lines; if one stays quiet, the traffic is not spread.
- **What to expect.** One api process held about 20 modelled companies (1.2 ingest requests a second of 20 spans plus 6 dashboard reads a second each) on the machine it was measured on. How that scales with replicas has not been measured, so watch latency and `503` rates after each step instead of assuming N replicas hold N times as much.

A revoked API key keeps working on each api replica for up to `API_KEY_CACHE_TTL_SECONDS` (10 s), since each replica caches key rows that long.

## Deploy from GitHub later

Once the repository is on GitHub, connect each service to it:

```bash
for s in api worker web; do
  railway service source connect --repo dheerajkarwasra/spanlight --branch main --service $s
done
```

Pushes to `main` then deploy automatically. The watch patterns in `railway.ts` rebuild only the services a commit touches: `backend/` changes the api and worker, `frontend/` and the Caddyfile change web. In each service's settings, turn on **Wait for CI** so a failing CI run skips the deploy.

Railway doesn't read `railway.ts` when it deploys. Apply changes to it yourself with `railway config apply --file deploy/railway/railway.ts`. To plan and apply them from pull requests, move the file to `.railway/railway.ts` and use the [`railwayapp/config`](https://github.com/railwayapp/config) GitHub Action.

## Settings without IaC

If `railway config` isn't an option, set the same things in each service's **Settings** tab in the dashboard, and set the variables from the table in step 3.

| Setting | api | worker | web |
|---|---|---|---|
| Builder, Dockerfile path | Dockerfile, `deploy/Dockerfile` | same | same |
| Start command | (image default) | `python -m app.jobs.worker` | (image default) |
| Pre-deploy command | `sh -c 'export DATABASE_URL="$MIGRATION_DATABASE_URL" && spanlight migrate && spanlight ensure-app-role --role spanlight_app'` | none | none |
| Healthcheck path, timeout | `/health/live`, 120 s | none | `/`, 60 s |
| Restart policy | On failure, 10 retries | same | same |
| Draining time | 15 s | 60 s | 10 s |
| Serverless | off | off | off |
| Public domain | none | none | generated or [custom](#custom-domain), port 8080 |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `railway config plan` fails with a CLI upgrade message, or can't find the package `railway` | The CLI must be 5.42.1 or newer (`brew upgrade railway`), and the SDK must be installed (`npm install --prefix deploy/railway`). Node must be 22 or newer. |
| `railway config apply` complains about a `preserve()` variable that doesn't exist | The six secrets (`SECRET_KEY`, `APP_DB_PASSWORD`, `METRICS_TOKEN`, `SENTRY_DSN`, `ANTHROPIC_API_KEY`, `CREDENTIALS_KEYS`) can't exist before the services do, and all six are set in step 3. On the first apply, comment out the `preserve()` lines, apply, do step 3, restore the lines and run `railway config plan`: it should show no changes. If it names an optional setting (`S3_BUCKET`, `BACKUP_DATABASE_URL`, `WORKER_METRICS_PORT` and so on), you uncommented its block before setting the variables: set them, or comment the block out again (see [Optional features](#optional-features)). On a later apply, a secret from step 3 is missing: set it with `railway variable set NAME --stdin --service <service>`. |
| The plan wants to delete a variable | It exists in Railway but not in `railway.ts`. Add it to the file, or let it go. |
| The api or worker build runs `npm ci` and the service starts Caddy | `SPANLIGHT_TARGET` isn't `backend` on that service, or `deploy/Dockerfile` lacks the `ARG SPANLIGHT_TARGET` line and final `FROM ${SPANLIGHT_TARGET}` stage. |
| Pre-deploy: `APP_DB_PASSWORD must be set to at least 16 characters` | Do step 3, then `railway up --service api` again. |
| Pre-deploy can't connect to the database | Check that Postgres is running and that `railway variable list --service api` shows `MIGRATION_DATABASE_URL` resolved to a `postgres.railway.internal` host. |
| api crashes with `SECRET_KEY must be set when APP_BASE_URL uses https` | Set `SECRET_KEY` (step 3). |
| api or worker crashes with `CREDENTIALS_KEYS is invalid: entry 1 …` | The value isn't `<key id>:<base64 of 32 bytes>`. The message names the entry and the rule it breaks and never prints the value. Fix the value on both services. If secrets are already sealed, edit the existing key back into shape; a freshly generated one can't open them. |
| Sign-up returns 403 `ORIGIN_NOT_ALLOWED` | `APP_BASE_URL` doesn't match the address in the browser. If the domain was generated after the api deployed, run `railway redeploy --service api`. For a custom domain, set `APP_BASE_URL=https://your.domain` on the api and add it to `railway.ts` (see [Custom domain](#custom-domain)). Extra origins go in `ALLOWED_ORIGINS` (comma-separated). |
| `/api/…` returns 502 from web | The api isn't live yet, or `API_UPSTREAM` on web (`GATEWAY_UPSTREAM` for `/gw/…`) isn't `api.railway.internal:8000`. Environments created before 16 October 2025 use IPv6-only private networking. There, uvicorn must listen on `::`: set the api start command to `/bin/sh -c "unset MIGRATION_DATABASE_URL; exec uvicorn app.main:app --host :: --port $PORT --proxy-headers --forwarded-allow-ips=*"`. |
| The worker restarts a few times on the first deploy | Expected until the api's pre-deploy has created `spanlight_app`. If it doesn't settle, check `railway logs --service worker --latest`. |
| The live demo says it isn't configured | `DEMO_ENABLED` isn't `true` on the api, or the api hasn't been redeployed since it was set. |
| No demo traffic arrives | `ANTHROPIC_API_KEY` is missing on the worker, or this month's $1 budget is spent. The worker logs say which. |
| Sentry shows no events from one service | `SENTRY_DSN` isn't set on that service, or the service hasn't redeployed since it was set. Check the name with `railway variable list --service <name> --kv \| cut -d= -f1` (a sealed variable isn't listed; look in the dashboard), set it as in step 3 if it's missing, then `railway redeploy --service <name>`. |
| Sessions show a private IP for everyone, and rate limits hit all visitors at once | `TRUSTED_PROXIES` isn't set on web, or `deploy/Caddyfile` lacks the `servers { trusted_proxies … client_ip_headers X-Real-IP }` block. |
