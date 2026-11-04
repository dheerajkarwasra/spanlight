# Roll back a deploy

## Purpose and when to use

Return to the previous version after a deploy that broke something: errors after the upgrade, a failing worker, a dashboard that does not load. If the problem is a lost or damaged database rather than bad code, use [restore.md](restore.md) instead.

The deciding fact is whether the bad deploy applied a database migration. Spanlight checks the schema at runtime: `/health/ready` compares the database's Alembic revision with the head revision of the code and answers `503` with `"migrations":"pending"` when they differ, in either direction. So:

- **No migration ran:** rollback is "check out the old code and redeploy". Quick and safe.
- **A migration ran:** the old code will not become ready on the newer schema, and `spanlight migrate` in the old build fails because Alembic does not know the database's newer revision. Choose a forward repair, a reviewed downgrade using the new build's migration files, or a restore into a new database. A downgrade is permitted only after every writer is drained and stopped, and a recoverable backup has been verified.

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

1. Enable maintenance at the ingress and pause producers. Drain active API/gateway requests and leased worker jobs, then stop every writer, including standalone gateways, schedulers, external integrations and all replicas. SDK retries are bounded and do not guarantee delivery across the maintenance window. Keep the database running; confirm no application transactions remain before DDL.

   ```bash
   spl --profile standalone-gateway stop web api worker gateway
   ```

2. Read what the migrations you are about to undo do on the way down. A `DROP TABLE` or `DROP COLUMN` is data you will lose.

   ```bash
   sed -n '/def downgrade/,$p' backend/alembic/versions/0015_add_worker_heartbeats.py
   ```

   Read every file from the current revision down to the target. Replace the file name with the real ones. If a downgrade deletes data you need, stop and restore a pre-deploy backup instead ([restore.md](restore.md)). One case to know: downgrading below `0508` drops the cache-write rates (from the surviving price overrides too) and deletes the prompt-size tier rows from `model_prices` and `price_overrides`, and the seeding on the next upgrade keeps the built-in rows that still exist, so they come back without write rates. After such a round trip, stop `api` and `worker` (a span that arrives while the table is empty gets an unknown cost), run `DELETE FROM model_prices` as the owner and then `spanlight sync-prices`, start them again, and set the write rates and tier rows of your overrides again.

3. Take a backup of the stopped-writer state, in case the downgrade goes wrong. Verify it restores into a disposable database before proceeding; a nonempty file or a backup listing alone does not prove recoverability. Keep the pre-deploy backup too. Follow [restore.md](restore.md) for the restore rehearsal and application checks.

   ```bash
   spl up -d postgres
   spl exec -T postgres pg_dump -U postgres -Fc --no-unlogged-table-data spanlight > spanlight-before-downgrade.dump
   ls -l spanlight-before-downgrade.dump
   ```

4. Only after the restore rehearsal succeeds and the downgrade has been reviewed against the target code, downgrade to the old head as the database owner. Use the already-built new image with the migration files; `--no-deps` prevents starting another service:

   ```bash
   spl run --rm --no-deps migrate alembic downgrade 0014
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

2. If a migration ran, put the instance into maintenance before changing the schema. Do not run a downgrade from a shell in a serving API replica.

   - Pause deploy automation and incoming producers. Block public/API/gateway ingress at the edge. Drain in-flight requests, buffered gateway recordings and leased worker jobs, then stop all API, worker, standalone gateway, scheduler and other writer replicas. Disable automatic restarts/redeploys while recovering. Check database sessions for active application transactions; maintenance is incomplete until all writers are stopped.
   - Take a database-owner backup of this quiescent state and preserve the pre-deploy backup. Restore the chosen backup into a separate disposable database and verify schema, data, RLS, encryption keys and application access using [restore.md](restore.md). A successful backup command alone is insufficient.
   - Review every intervening `downgrade()` and the target application's schema expectations. Rehearse the downgrade and target build against a restored copy. If the downgrade drops required data, is unsupported, or cannot be proven compatible, use a forward repair or restore the pre-deploy backup into a **new database**. Record the restore's data-loss window and reconcile producers before reopening ingress.
   - For a proven compatible downgrade, run a temporary one-off service from the **new image digest**, with its migration files and a database-owner URL. It must have no public route, no worker command, no automatic upgrade pre-deploy hook, and no automatic restart. Do not reuse the normal API start command. In that isolated migration service only, run:

     ```bash
     cd /app
     DATABASE_URL="$MIGRATION_DATABASE_URL" alembic downgrade 0014
     ```

     Replace `0014` with the recorded target revision. Supply the owner URL only to the migration task; never print it. Verify successful completion and `alembic_version` before deploying the compatible old API, worker and web images. If restoring to a new database, update the application's database binding and required keys while writers remain stopped.
   - Start the compatible API and worker behind maintenance, verify readiness and a controlled ingest/read round trip, then restart any other writers. Remove the temporary migration service and its credentials. Restore ingress and producers only after the verification below passes. Resume deploy automation deliberately; it must not immediately redeploy the bad version.

3. Railway's dashboard also lists past deployments of each service and can redeploy an earlier one. That only changes the code, so it has the same limit: use it only when no migration ran.

## Verification

- `/health/ready` is `200`: database and migrations `ok`, and `worker_heartbeat_age_s` a few seconds (allow up to two minutes after the worker restarts).
- `alembic_version` holds the old head revision.
- Tenant isolation and encrypted data access work with the application role.
- The worker logs `worker_started`, and `jobs` has fresh `done` rows (see [README.md](README.md#is-the-worker-alive)).
- Your applications' spans appear again in the dashboard, and the error rate is back to normal.

## Escape hatch

- **A downgrade failed:** keep maintenance enabled and all writers stopped. Inspect the actual database revision and schema; do not assume every migration or external effect was rolled back. Fix forward if safe, or restore the verified pre-deploy backup into a new database ([restore.md](restore.md)) and point the compatible application at it. Recheck readiness, RLS, keys, jobs and real ingest before resuming traffic.
- **You rolled back and it is still broken:** the problem is probably not the code. Check the secrets and settings you changed ([rotate-secrets.md](rotate-secrets.md)) and `deploy/.env`.
- **Downgrading all the way (`alembic downgrade base`):** this drops every Spanlight table and type but leaves the `citext` extension installed, on purpose: the first migration's downgrade does not drop extensions, because another schema in the same database may use them. It is harmless, since `spanlight migrate` creates the extension with `IF NOT EXISTS`. If you want a bare database and nothing else uses it, run `DROP EXTENSION citext;` as the owner.
- **Roll forward instead:** if the bug is small, a patch release through [deploy.md](deploy.md) is often safer than a downgrade.

### Aggregate coverage (1027)

Keep migration 1027 when rolling application binaries back: old writers must stay stopped while
source preservation is required. A schema downgrade refuses if any incomplete-hour evidence
exists or any project has unknown/legacy coverage. Only an empty inventory or projects whose
boundaries all equal the proven-new UTC epoch, with no incomplete markers, can downgrade.
Do not delete markers or rewrite boundaries to bypass this guard. Retention cannot remove this
evidence, because the marker remains necessary after both raw rows and aggregate snapshots age out.
