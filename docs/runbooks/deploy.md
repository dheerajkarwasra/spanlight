# Deploy a new version

## Purpose and when to use

Ship a new Spanlight build to a running instance: apply database migrations, replace the api, worker and web containers, and confirm the result. Use it for every upgrade, including a patch release.

Migrations run before the new api starts and are applied with `spanlight migrate` (Alembic `upgrade head`). Review old/new schema compatibility before deployment. For incompatible or destructive changes, enable maintenance, pause producers, drain requests and jobs, and stop all API, worker, gateway and other writers before DDL. Verify a backup restores successfully into a disposable database first; [rollback.md](rollback.md) explains what a rollback costs once a migration has run.

Migrations 1031–1033 (budget snapshots) are such a change: stop the api, any standalone gateway and the worker before `spanlight migrate`. With old and new versions mixed, a call an old process settles after the new reservation trigger is in place is missing from the open hour's budget total until another write to that hour, and 1032's triggers on `spans`, `traces` and `budget_reservations` must wait for running writers within its 5-second lock timeout.

## Worker kind and payload compatibility

Use a maintenance/drained upgrade by default: pause producers, drain requests and jobs,
stop old writers and workers, apply the reviewed migration, then start and verify the new
workers before resuming producers. The queue's registry filter is a narrow kind guarantee,
not proof of rolling schema or payload compatibility.

Workers claim and expire exhausted leases only for their exact registered handler keys,
including enterprise handlers. Unsupported kinds wait without consuming attempts or changing
payloads, fences or leases; an empty registry processes nothing. Compare queued/running kinds
with the `tasks` list in each worker's `worker_started` log (see [README.md](README.md#is-the-worker-alive)).
A healthy heartbeat or successful empty poll does not prove a capable worker exists for every kind.

The first release containing this filter still requires draining and replacing every older,
unfiltered worker before any new kind is produced. For a breaking payload contract, use a
distinct kind with an explicit handler, or complete a drained cutover. Evolve a payload within
one kind only after both old-handler/new-payload and new-handler/old-payload tests pass.
An added version field cannot protect against deployed handlers that ignore it.

## Prerequisites

### Aggregate capture and repair cutover

Revision 1028 requires a drained maintenance release. Keep external maintenance or traffic
control enabled while stopping old API, standalone gateway, ingestion, usage-backfill and
worker producers. Stop old schedulers too. Verify the old processes and active jobs have
finished or have an explicit recovery disposition before starting replacement writers.
Resolve old payloads through compatible new handlers or a reviewed offline migration;
do not delete queued jobs to make a deployment appear healthy.

Apply the reviewed expand-only 1028 migration before the new application code, then verify
the exact migration head and that the application role is neither superuser nor BYPASSRLS.
Start the API and check `/health/live`, start the compatible workers, then require the existing
`/health/ready` gate before restoring traffic. API startup liveness cannot depend on a worker
that the deployment has not started yet. A heartbeat alone proves neither payload compatibility
nor completion of aggregate repair.

Normal materialized observation service requires both aggregate capture and repair. The safe
code default `AGGREGATE_REPAIR_ENABLED=false` keeps capture active but leaves materialized
observations degraded. After the schema, writer and worker gates, enable repair through the
approved configuration, compare complete-source buckets against authoritative raw results,
and require the relevant enabled-consumer backlog to converge. Disabled-consumer obligations
remain pending; complete-source raw views remain usable. Do not declare materialized alerts
or observations restored solely because the API is live.

Rollback disables repair while retaining capture and truthful pending reads. Rolling back
capture creates an untracked interval requiring drain and reconciliation. Downgrade 1028 only
with all marker writers and consumers stopped and no dirty rows, including zero-mask remnants.
Preserve the independent 1026 budget and 1027 source-coverage boundaries.

Compose dependency ordering and Railway's startup healthcheck do not implement this maintenance
workflow for existing replicas. External traffic control and the documented operator sequence
are required. Hosted enforcement and staging verification remain release gates; this procedure
does not claim automated rolling compatibility.

- Shell access to the host that runs Docker Compose, or the Railway CLI logged in (`railway whoami`) and linked to the project.
- The version you are shipping, as a git tag or commit in this checkout.
- For a backup: backup storage (`BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`) and `BACKUP_DATABASE_URL` configured. Without them there is no automatic backup; take a `pg_dump` yourself (step 2).
- Five minutes with nobody else changing `deploy/.env`.

## Docker Compose

Define the shortcut once per shell:

```bash
spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
```

### 1. Record where you are now

You need this to roll back.

```bash
git rev-parse --short HEAD
spl exec postgres psql -U postgres -d spanlight -tA -c "SELECT version_num FROM alembic_version"
curl -fsS "$BASE/health/ready"
```

Write down the commit and the revision (for example `0015`). Expected last line: `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`. If the instance is not healthy before you start, fix that first (see [README.md](README.md#first-five-minutes-of-any-incident)).

### 2. Take a backup

With backups configured:

```bash
spl exec worker spanlight backup now
```

Expected: `Backed up to backups/2026/10/09/spanlight-20261009T143012Z.dump (48213377 bytes).` (your own key and size). Confirm it is listed:

```bash
docker run --rm --network <compose-network> --env-file <recovery.env> <deployment-image> spanlight backup list
```

Without object storage, dump the database to a file on the host. The image's `pg_dump` is version 17, the same as the server, so run it in the `postgres` container:

```bash
spl exec -T postgres pg_dump -U postgres -Fc --no-unlogged-table-data spanlight > spanlight-before-deploy.dump
ls -l spanlight-before-deploy.dump
```

`-T` keeps the terminal out of the binary dump. The file must not be empty. It holds every prompt and completion: store it privately and delete it when the deploy is confirmed good.

### 3. Get the new code

```bash
git fetch --tags
git checkout <tag-or-commit>
```

Read what changed in `deploy/.env.example` between the old and new versions. A new required variable stops the stack from starting with `set <NAME>` in the error; add it to `deploy/.env`:

```bash
git diff <old-commit> HEAD -- deploy/.env.example deploy/compose.yaml
```

### 4. Build and start

For an incompatible migration, keep ingress blocked and all old writers stopped through migration and verification. `depends_on` orders new containers; it does not drain existing replicas. Stop standalone gateways and external writers too. Follow the maintenance sequence in [rollback.md](rollback.md#steps-railway), adapted to this host.

```bash
spl up -d --build
```

Compose rebuilds the images, then runs `migrate` to completion (it applies migrations as the database owner and refreshes the `spanlight_app` role). `api` and `worker` start only after `migrate` succeeds, and `web` starts once `api` is healthy. The containers whose image or environment changed are recreated, so requests can fail for a few seconds. The Python SDK retries network errors, 429 and 5xx with backoff (`max_retries=5` by default), so a short gap does not lose spans from SDK clients.

### 5. Check that migrations ran

```bash
spl logs migrate
```

Expected, in this order:

```
Database is at the latest migration.
Role spanlight_app is ready (no superuser, no RLS bypass).
```

If `migrate` failed, the api never started. Read the error above those lines, then go to [Escape hatch](#escape-hatch).

### 6. Verify

```bash
spl ps
curl -fsS "$BASE/health/ready"
spl logs --since 2m worker | grep worker_started
```

Expected:

- `spl ps`: `postgres`, `api`, `worker` and `web` running; `api` healthy; `migrate` exited with code 0.
- `/health/ready`: `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}`.
- The worker log has one `worker_started` line listing its tasks.

Then check real traffic: open the dashboard, sign in, and confirm new spans arrive from one of your applications (the newest trace is minutes old, not hours). Check the first ten minutes of error rate on the api:

```bash
spl logs --since 10m api | grep -c '"level": "error"'
```

Logs are JSON lines. A handful of errors is a signal to look; a stream of them is a signal to [roll back](rollback.md).

Do not use `deploy/smoke.sh` to verify production. It builds and then deletes (`down -v`) its own stack on port `WEB_PORT`, which collides with the running one. Run it only on a separate machine or a CI runner.

### 7. Clean up

When the deploy is good for at least a day, delete the pre-deploy dump file from the host. Old Docker images stay on disk until you prune them:

```bash
docker image prune
```

## Railway

For a backward-compatible migration, deploy the api first: its pre-deploy command runs `spanlight migrate && spanlight ensure-app-role --role spanlight_app && spanlight activate-aggregate-coverage --writers-drained --fresh-install --skip-if-projects-exist` as the Postgres owner (the last step activates aggregate coverage on a fresh install and changes nothing once a project exists), and a failure there stops the deploy and leaves the running version untouched.

For an incompatible or destructive migration, pause automatic deploys, enable maintenance, drain and stop every writer, verify a restorable backup, and run a reviewed one-off migration before starting compatible application images. Use the isolation and recovery sequence in [rollback.md](rollback.md#steps-railway). A failed pre-deploy can leave old replicas running; it is not protection from incompatible DDL.

1. Record the current state and take a backup.

   ```bash
   git rev-parse --short HEAD
   curl -fsS https://$DOMAIN/health/ready
   ```

   Write down the commit. The Railway setup has backups off: the object-storage and backup settings are commented-out opt-in blocks in `railway.ts`, so the nightly backup job ends `skipped_not_configured` until you enable them (see [Optional features](../deploy/railway.md#optional-features) in the Railway guide). If backups are configured, run one now from a shell inside the service (`railway ssh --service worker`, then `spanlight backup now`); it prints `Backed up to <key> (<n> bytes).`. If they are not, use Railway's own backup of the Postgres service from the dashboard, or a `pg_dump -Fc` against the database from a host that can reach it.

2. Check out the new version and apply any infrastructure change.

   ```bash
   git checkout <tag-or-commit>
   railway config plan --file deploy/railway/railway.ts
   ```

   The plan must show only changes you expect. Apply with `railway config apply --file deploy/railway/railway.ts`. A variable that exists in Railway but is missing from `railway.ts` is deleted by an apply, so read the plan before applying.

3. Deploy in this order.

   ```bash
   railway up --service api
   railway up --service worker
   railway up --service web
   ```

   Each command exits 0 once its deployment is live. In the api's deploy logs, the pre-deploy step prints `Database is at the latest migration.` and `Role spanlight_app is ready (no superuser, no RLS bypass).`; the api goes live once `/health/live` answers 200 (its Railway healthcheck is liveness only, because `/health/ready` also needs a worker heartbeat and the worker deploys after the api). `railway logs --service api --latest` shows the logs of a failed deploy.

4. Verify.

   ```bash
   curl -fsS https://$DOMAIN/health/ready
   railway logs --service worker --lines 200 | grep worker_started
   ```

   Expected: `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0}` and a `worker_started` line from the new worker. Until the worker has deployed and beaten once, `/health/ready` is a `503` for that reason alone; wait for the worker deploy before you judge it.

If you deploy from GitHub (see [the Railway guide](../deploy/railway.md)), pushes to `main` deploy automatically; Railway does not read `railway.ts` on deploy, so run `railway config apply` yourself for infrastructure changes.

## Release verification

A server `v<semver>` tag invokes the same CI workflow used by pull requests at the tagged commit. The required aggregate depends on backend, both SDKs, frontend, docs, workflow checks, Docker builds and Compose smoke. Every dependency must report `success`; failed, skipped, cancelled or incomplete checks block publication. The publish job additionally requires the aggregate's verified SHA to equal the event SHA. A green run for another commit or branch head does not qualify. SDK tag trains remain separate.

The release job builds and loads backend/web candidates once, runs the disposable Compose smoke against those local images with builds removed and pulling disabled, checks image IDs stayed unchanged, then scans and pushes those same images. Recorded registry digests feed signing and SBOM generation; stable `latest` moves only after signing. Pre-releases never move `latest`.

Before authorizing a release, review the workflow run and retain its tagged SHA, all required results, candidate smoke/scan logs and both pushed digests with signature/SBOM verification. A disposable Docker rehearsal and an owner-authorized RC run are required execution evidence: static contract tests and workflow lint alone do not prove hosted publication or digest continuity. Rehearse a deliberately failing required check and confirm no image or release is published; then use a successful RC to verify the tested image configuration IDs and published digest contents correspond. Never use production volumes for the smoke script, which deletes its test volumes.

## Escape hatch

- **`migrate` failed or the new api does not become ready, and no migration ran:** the old containers are gone on Compose (the images were rebuilt), so go back to the previous commit and rebuild: `git checkout <old-commit>` then `spl up -d --build`. On Railway the previous api deployment keeps serving because the pre-deploy failed.
- **The new version is live and wrong:** follow [rollback.md](rollback.md). If a migration ran, read it first: the old build refuses to become ready on a newer schema.
- **Data is damaged:** follow [restore.md](restore.md) with the backup from step 2.

### Activate aggregate source preservation (1027)

Migration 1027 leaves aggregate coverage unverified. Deploy the instrumented API, worker,
retention, erasure and administrative writers, and stop/drain every older writer before
running `spanlight activate-aggregate-coverage --writers-drained` as the database owner.
Do not treat a successful migration alone as activation. The command uses the database clock
plus the accepted ten-minute future skew and advances legacy coverage to the next UTC day.
Older buckets remain unverified permanently; refreshing or backfilling never clears evidence.
For a new installation, add `--fresh-install`; activation refuses a nonempty project inventory.
The Compose `migrate` service runs it this way on every `up` with `--skip-if-projects-exist`, so a
new Compose install is activated before its first project exists; with any project present that
step changes nothing and exits 0, and the drained activation above stays the upgrade path.
New project IDs created after activation use the finite UTC Unix epoch as their boundary:
they have no earlier source history, and accepted telemetry is bounded to the past 90 days.
The finite value round-trips through ordinary project ORM/API reads.

**One-time step for an install that had projects before 1027 (spanlight.dev included).** After
the deploy that removed the pre-1027 writers, run once as the database owner:

```bash
# Railway: railway ssh --service api, then
DATABASE_URL="$MIGRATION_DATABASE_URL" spanlight activate-aggregate-coverage --writers-drained
```

Check it worked: `curl -fsS https://$DOMAIN/health/ready` shows `"aggregate_coverage":"active"`.
While it says `"inactive"`, the api and worker log `aggregate_coverage_inactive` at start and the
dashboards read rollup figures as unknown. The field is informational and never fails readiness.

Source deletion and aggregate replacement serialize per project. Deletion records every affected
trace-start and span-start hour in the same transaction; existing aggregate snapshots survive.
Incomplete reads expose `aggregate_status=unavailable` and `oldest_pending_at=null` on objects.
Bare-list routes keep their bodies and expose `Aggregate-Status`, even for `[]`; the optional
`Aggregate-Oldest-Pending-At` header is absent when no correction is pending. Missing values
are null, never inferred zero. There is no pending correction consumer in this release.
Monitor `spanlight_aggregate_unavailable_reads_total` for reads requiring retained snapshots.
The dashboard is same-origin; no cross-origin header exposure is needed.
