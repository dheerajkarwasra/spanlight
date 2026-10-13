# Roll back a deploy

## Purpose and when to use

Return to the previous version after a deploy that broke something: errors after the upgrade, a failing worker, a dashboard that does not load. If the problem is a lost or damaged database rather than bad code, use [restore.md](restore.md) instead.

The deciding fact is whether the bad deploy applied a database migration. Spanlight checks the schema at runtime: `/health/ready` compares the database's Alembic revision with the head revision of the code and answers `503` with `"migrations":"pending"` when they differ, in either direction. So:

- **No migration ran:** rollback is "check out the old code and redeploy". Quick and safe.
- **A migration ran:** the old code will not become ready on the newer schema, and `spanlight migrate` in the old build fails because Alembic does not know the database's newer revision. You must first undo the migration with the new build's own `downgrade()` functions, or restore a backup taken before the deploy.

## Prerequisites

- The commit and Alembic revision you wrote down in step 1 of [deploy.md](deploy.md). If you did not, find them below.
- A backup taken before the deploy (deploy.md step 2). A downgrade can drop tables and columns, and with them data.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

## Steps (Docker Compose)

### 1. Did a migration run?

Revision in the database now, and the newest revision the old code knows:

```bash
spl exec postgres psql -U postgres -d spanlight -tA -c "SELECT version_num FROM alembic_version"
git ls-tree --name-only <old-commit> backend/alembic/versions/ | tail -1
```

The first command prints the current revision, for example `0015`. The second prints the newest migration file in the old commit, which is its head (for example `backend/alembic/versions/0014_add_exports.py`, so revision `0014`). If the two match, go to [No migration ran](#no-migration-ran). If the database is ahead, go to [A migration ran](#a-migration-ran).

### No migration ran

1. Check out the old version and rebuild.

   ```bash
   git checkout <old-commit-or-tag>
   spl up -d --build
   ```

2. Verify.

   ```bash
   spl logs migrate
   curl -fsS "$BASE/health/ready"
   spl logs --since 2m worker | grep worker_started
   ```

   Expected: the two `migrate` lines (`Database is at the latest migration.` and `Role spanlight_app is ready (no superuser, no RLS bypass).`), `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`, and one `worker_started` line.

### A migration ran

Do not check out the old code yet: the downgrade needs the new code's migration files, which are in the image you are running now.

1. Stop the services that write, so nothing changes the schema under you. Spans sent in the meantime are retried by the Python SDK (backoff on 5xx and network errors); other clients get connection errors.

   ```bash
   spl stop web api worker
   ```

2. Read what the migrations you are about to undo do on the way down. A `DROP TABLE` or `DROP COLUMN` is data you will lose.

   ```bash
   sed -n '/def downgrade/,$p' backend/alembic/versions/0015_add_worker_heartbeats.py
   ```

   Read every file from the current revision down to the target. Replace the file name with the real ones. If a downgrade deletes data you need, stop and restore a pre-deploy backup instead ([restore.md](restore.md)).

3. Take a backup of the current state, in case the downgrade goes wrong.

   ```bash
   spl up -d postgres
   spl exec -T postgres pg_dump -U postgres -Fc --no-unlogged-table-data spanlight > spanlight-before-downgrade.dump
   ls -l spanlight-before-downgrade.dump
   ```

4. Downgrade to the old head, as the database owner, using the image that is already built (the `migrate` service connects as the owner):

   ```bash
   spl run --rm migrate alembic downgrade 0014
   ```

   Replace `0014` with the old head revision from step 1. Expected output ends with an Alembic line for each undone migration, for example `Running downgrade 0015 -> 0014, add worker heartbeats`. A non-zero exit and a traceback mean the downgrade stopped: read the error, then go to [Escape hatch](#escape-hatch).

5. Check out the old code and start everything.

   ```bash
   git checkout <old-commit-or-tag>
   spl up -d --build
   ```

6. Verify.

   ```bash
   spl exec postgres psql -U postgres -d spanlight -tA -c "SELECT version_num FROM alembic_version"
   curl -fsS "$BASE/health/ready"
   spl logs --since 2m worker | grep worker_started
   ```

   Expected: the old head revision, `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`, and a `worker_started` line.

## Steps (Railway)

1. If no migration ran, redeploy the old code, api first:

   ```bash
   git checkout <old-commit-or-tag>
   railway up --service api
   railway up --service worker
   railway up --service web
   ```

   The api's pre-deploy runs `spanlight migrate` and `spanlight ensure-app-role`; on an unchanged schema both print their usual success lines. Verify with `curl -fsS https://$DOMAIN/health/ready`.

2. If a migration ran, deploying the old code first is harmless but useless: the old api's pre-deploy fails and Railway keeps the new deployment running. Undo the migration first, from a shell inside the running api service, where the new code's migration files are. The downgrade runs while the new-code api and worker are still live, so expect errors in their logs (and failing requests) until the old code is deployed; if you can, scale the worker down first so it stops writing (set its replica count to 0 in the service's settings, and restore it after step 1).

   ```bash
   railway ssh --service api
   ```

   Inside it:

   ```bash
   cd /app && DATABASE_URL="$MIGRATION_DATABASE_URL" alembic downgrade 0014
   exit
   ```

   `MIGRATION_DATABASE_URL` is the Postgres owner's URL, which the api process itself never sees (the image's start command unsets it). Replace `0014` with the old head. Right after the downgrade the new api's `/health/ready` returns 503 (its revision no longer matches the code), so deploy the old code at once (step 1). Read the downgrade functions first, as in the Compose steps: they can delete data.

3. Railway's dashboard also lists past deployments of each service and can redeploy an earlier one. That only changes the code, so it has the same limit: use it only when no migration ran.

## Verification

- `/health/ready` is `200`: database and migrations `ok`, and `worker_heartbeat_age_s` a few seconds (allow up to two minutes after the worker restarts).
- `alembic_version` holds the old head revision.
- The worker logs `worker_started`, and `jobs` has fresh `done` rows (see [README.md](README.md#is-the-worker-alive)).
- Your applications' spans appear again in the dashboard, and the error rate is back to normal.

## Escape hatch

- **A downgrade failed halfway:** `backend/alembic/env.py` runs the whole command in one transaction and PostgreSQL DDL is transactional, so a failed downgrade changes nothing. Read the error, fix its cause, and run the same command again. If you cannot, restore the pre-deploy backup into a new database ([restore.md](restore.md)) and point `DATABASE_URL` at it.
- **You rolled back and it is still broken:** the problem is probably not the code. Check the secrets and settings you changed ([rotate-secrets.md](rotate-secrets.md)) and `deploy/.env`.
- **Downgrading all the way (`alembic downgrade base`):** this drops every Spanlight table and type but leaves the `citext` extension installed, on purpose: the first migration's downgrade does not drop extensions, because another schema in the same database may use them. It is harmless, since `spanlight migrate` creates the extension with `IF NOT EXISTS`. If you want a bare database and nothing else uses it, run `DROP EXTENSION citext;` as the owner.
- **Roll forward instead:** if the bug is small, a patch release through [deploy.md](deploy.md) is often safer than a downgrade.
