# Restoring a database backup

Spanlight dumps its database every night at 03:00 UTC with `pg_dump -Fc --no-unlogged-table-data`
and stores the dump in your object storage under `backups/YYYY/MM/DD/spanlight-<UTC timestamp>.dump`.
Retention is a separate operator command; see [Credential isolation and retention](#credential-isolation-and-retention).

A restore always goes into a new, empty database: `spanlight restore` refuses a database that
already has any relation outside the system schemas, so it cannot overwrite live data. A restored
database is not ready to serve until it has been reconciled: it holds jobs that were running,
notifications that were about to be sent, provider calls that may or may not have happened and
schedules that would fire again. `spanlight restore-reconcile` quarantines all of that, records
your decisions and checks the result before you reopen. Nothing in this procedure starts a
service; you do, at the end, with the producers off.

## Recovery objectives (local drill, 2026-11-02)

These numbers come from the drill in `backend/tests/backups/test_restore_drill.py`, run on a
developer laptop against an invocation-owned PostgreSQL 17 container and a MinIO container, with
the real backup job and the real restore. They describe that drill, not a hosted deployment, and
they are an envelope, not a service-level promise.

| Measure | Value | How it was measured |
| --- | --- | --- |
| Recovery point (RPO) | up to **24 h + the backup window**, plus any missed nightly run | One dump a day at 03:00 UTC; everything written after the dump's snapshot is lost, and a failed or skipped backup doubles the window. The snapshot instant is not recorded, so treat the interval from the job's start to its upload as the uncertain window (0.6 s in the drill, longer on real data). |
| Database | 14.3 MB (`pg_database_size`), dump 420 KB | Two tenants, four traces, sealed secrets under two keys, audit chains, queued and running jobs, pending and failed notifications, running experiment and evaluator runs, every producer on. |
| Backup (`backup_database` job) | 0.6 s | From job claim to upload complete (420,641-byte dump, schema revision 1035). |
| Restore (`spanlight restore --receipt`) | 1.0 s | Includes the emptiness check and `pg_restore --single-transaction`. |
| `restore-reconcile prepare` | 0.03 s | Verification (keys, audit chains, RLS, role) plus quarantine of 16 entities. |
| `restore-reconcile apply` + `check` | 0.02 s | Complete decision file, then the read-only release check. |
| Recovery time (RTO), machine part | **1.0 s** restore through check | Restore + prepare + apply + check. On a real database this scales with the dump size; measure it with your own data (`cd backend && uv run pytest -m serial -s tests/backups/test_restore_drill.py` prints a `DRILL` line). |
| Recovery time, human part | **not measured** | Writing the decision file (one disposition per quarantined entity and per external window) is operator work and dominates a real recovery. Rehearse it. |

Measured with `pg_restore` 17.11 in the client container, load averages 2.5 / 3.7 / 9.1 before the run; the drill prints the raw `DRILL` line (`cd backend && uv run pytest -m serial -s tests/backups/test_restore_drill.py`). The
hosted deployment has not been drilled; see [Owner steps](#owner-steps-before-the-hosted-service-relies-on-this).

## Before you start

- The backup job needs explicit backup storage (`BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`) and
  `BACKUP_DATABASE_URL`. The tenant tables have row-level security forced on and `pg_dump` turns
  it off for the dump, which fails unless the role can bypass it: use a superuser or a role with
  `BYPASSRLS`, never the application role. Managed PostgreSQL services usually have no true
  superuser, so create a `BYPASSRLS` role there. With
  `BACKUPS_ENABLED=false`, or without either setting, the nightly job ends `done` with outcome
  `skipped_not_configured` and takes no backup.
- Set a lifecycle rule on the bucket that aborts incomplete multipart uploads after a day
  (`AbortIncompleteMultipartUpload`, `DaysAfterInitiation: 1`). A crash in the middle of a backup
  can leave uploaded parts behind, and they cost storage until the rule removes them.
- A dump contains every prompt and completion your applications sent. Keep the bucket private and
  encrypted at rest, and restore only onto infrastructure you trust.
- Recovery commands require a separate List/Get principal through `BACKUP_S3_*`, supplied only
  to the operator process. Routine workers have Put/Abort permission and cannot list or restore.
  Use a one-off deployment image with an operator-only environment file; do not `exec worker`.
  The image includes `pg_restore` 17. Outside the image you need PostgreSQL client tools 17.
- The operator environment also needs `CREDENTIALS_KEYS` with **every** key the backup's sealed
  values were written under (current and historical). `prepare` opens each sealed value and
  fails on a missing or wrong key; a restore whose secrets cannot be read is not a recovery.
- Keep a durable operator directory for the receipt, the context, the manifest, the decision
  file, the evidence files and the apply plan. The commands publish these files before they
  change the database and never replace a file with different content. The directory must
  support hard links (a network mount that refuses them makes the publish fail, closed). Copy
  the files somewhere independent as you go: with the manifest lost after `prepare` has
  committed, no command can continue and you restore again into another new database.
- Run `prepare`, `apply` and `check` over the same connection path (same host spelling, TCP or
  socket): the manifest binds the server-side endpoint, and a different path reads as a
  different target and fails closed.

If you run the Docker Compose stack in `deploy/compose.yaml`, follow
[Restoring on Docker Compose](#restoring-on-docker-compose): it does the same steps with the
container names and the fixed database name that file uses. The steps below are for any other
deployment.

## Steps

1. **Stop everything and hold the boundary.** Stop the API, the gateway, every worker, the prune
   schedule and any automatic deploy or restart hook. Hold upstream ingest and client retry
   queues. Note the time: this is the *isolation time*. Nothing may write to the old or the new
   database until step 10. The commands cannot discover processes; `--all-services-held` is your
   attestation that this is true.

2. **List the backups.**

   ```bash
   spanlight backup list
   ```

   Each line is the key, the size in bytes and the upload time. Pick the key you want, usually the
   newest one before the incident. Note the backup job's start and upload times from the worker
   log or the `jobs` table: they bound the dump's snapshot.

3. **Create a fresh, empty database** on a PostgreSQL 17 server, and record that you created it
   now (the command and its time go into the context file as `target_creation_evidence`). The
   emptiness check is necessary, not sufficient: it cannot tell a new database from an old one
   that was emptied. Never reuse a failed, customer or development database.

   ```bash
   psql "$ADMIN_URL" -c 'CREATE DATABASE spanlight_restored'
   ```

4. **Restore into it, with a receipt.** Pass the URL in the environment rather than on the
   command line, so the password stays out of the process list and your shell history:

   ```bash
   export RESTORE_TARGET_URL='postgresql://postgres:<password>@<host>:5432/spanlight_restored'
   spanlight restore --key backups/2026/10/09/spanlight-20261009T030000Z.dump --receipt /srv/recovery/receipt.json
   ```

   The URL must name the host and the database. The command streams the dump from object storage
   straight into `pg_restore --no-owner --no-privileges --single-transaction`, so no disk space is
   needed for the dump and a failure leaves the target empty. The receipt holds the SHA-256 and
   size of the exact bytes `pg_restore` consumed, the target and the time bounds; `prepare`
   requires it. If the restore succeeded but the receipt could not be written, do not read the
   key again and call that the evidence: restore again into another new database.

5. **Create the application role and bring the schema up to date.** Dumps carry neither owners
   nor privileges, so the restored database has no role the API and worker can use. As the
   database owner:

   ```bash
   export DATABASE_URL="$RESTORE_TARGET_URL" APP_DB_PASSWORD='<at least 16 characters>'
   spanlight migrate          # only when the backup is older than the release you run
   spanlight ensure-app-role
   ```

   Roles belong to the whole PostgreSQL server, not to one database, and this command sets the
   role's password. If the restored database is on the same server as a deployment that is still
   running, use that deployment's `APP_DB_PASSWORD`: a different value changes the password the
   running API and worker connect with, and they stop working. Row-level security only protects
   tenants when the application connects as this role, never as a superuser.

   Any rename of the database happens now, before `prepare`: `prepare` binds the database's name
   and OID, and a later rename is a hard mismatch. If you renamed it, record the name at restore
   time as `renamed_from` in the context file.

6. **Write the context file.** It holds what only you know; every field is required so that a
   missing fact stays unresolved instead of defaulting to "nothing happened":

   ```json
   {
     "format": "spanlight-recovery-context/1",
     "operator": "<your name>",
     "target_created_at": "2026-10-09T09:12:00+00:00",
     "target_creation_evidence": "CREATE DATABASE spanlight_restored by <name>, psql session log",
     "renamed_from": null,
     "backup_started_at": "2026-10-09T03:00:00+00:00",
     "backup_finished_at": "2026-10-09T03:04:10+00:00",
     "isolation_at": "2026-10-09T09:00:00+00:00",
     "release_at": "2026-10-09T11:00:00+00:00",
     "services_held": ["api", "gateway", "worker", "prune schedule", "deploy hook"],
     "upstream_retries_held": true,
     "windows": [
       {"id": "openai-acme", "kind": "provider_account", "description": "OpenAI account of Acme",
        "from_at": "2026-10-09T03:00:00+00:00", "to_at": "2026-10-09T09:00:00+00:00"}
     ],
     "verification_evidence": ["row counts compared with the 2026-10-08 report"]
   }
   ```

   `windows` lists every external account, destination, producer and upstream retry queue whose
   effects between the backup and the isolation are not in the restored database: each becomes
   an entity that needs a decision. `release_at` is when you plan to reopen; an authorized
   notification replay becomes due then.

7. **Prepare: verify and quarantine.**

   ```bash
   spanlight restore-reconcile prepare --manifest /srv/recovery/manifest.json \
     --receipt /srv/recovery/receipt.json --context /srv/recovery/context.json --all-services-held
   ```

   The command takes the target's recovery lock and, in one transaction, verifies the restored
   database (every sealed value opens with `CREDENTIALS_KEYS`, every organization's audit chain
   is intact and its head recorded, row-level security is forced on `traces` and `spans`, the
   app role is unprivileged, the unlogged rate-limit table is empty), inventories the restored
   work, publishes the manifest and quarantines:

   | Restored rows | Quarantine image |
   | --- | --- |
   | Every queued or running job, whatever its kind | `failed`, lease cleared, fence +1 once, `last_error = recovery:<id>:unresolved`, outcome cleared; payload, attempts and dedupe key kept |
   | Every queued or running experiment or evaluator run | `failed` with the same marker, `finished_at` set, heartbeat cleared; an evaluator reservation becomes a conservative cost estimate (`max(cost, reserved)`), not verified billing |
   | Every pending or failed notification | `failed`, lease cleared, fence +1 once, the same marker; sent rows untouched |
   | Every experiment schedule, evaluator, alert rule, project digest setting | off (`schedule = NULL`, `enabled = false`, `weekly_digest_enabled = false`); prior values in the manifest |
   | Every audit stream | disabled with the marker; `delivered_seq` unchanged |

   The manifest also lists one entity per context window and one per ISO week of every scheduled
   experiment between the backup and the release time (occurrences that may be missing). It
   contains identifiers, state fields and payload hashes, never secrets or message bodies. The
   database `COMMENT` now carries the recovery id and the manifest digest; the original comment
   is kept in the manifest. Running `prepare` again with the same manifest changes nothing and
   never bumps a fence twice; if the manifest was published but the commit was lost, the rerun
   applies the same plan once. A different target, an unrelated marker, a changed schema or a row
   that no longer matches its preimage fails closed.

8. **Decide, then apply.** Write one immutable decision file with exactly one decision per
   manifest entity:

   ```json
   {
     "format": "spanlight-recovery-decisions/1",
     "recovery_id": "<from the manifest>",
     "manifest_sha256": "<sha256 of manifest.json>",
     "decisions": [
       {"entity": "job:4821", "disposition": "suppress", "operator": "<name>",
        "decided_at": "2026-10-09T10:20:00+00:00", "reason": "retention run; the scheduler will create a fresh one"},
       {"entity": "notification:0192…", "disposition": "authorize_replay", "accepts_duplicate": true,
        "operator": "<name>", "decided_at": "2026-10-09T10:21:00+00:00", "reason": "receiver has no record; duplicate acceptable"},
       {"entity": "experiment_run:0192…", "disposition": "confirmed_completed", "operator": "<name>",
        "decided_at": "2026-10-09T10:25:00+00:00", "reason": "provider ledger shows the request",
        "evidence": {"ref": "openai-ledger-2026-10-09.csv", "sha256": "<sha256 of that file>"}},
       {"entity": "audit_stream:0192…", "disposition": "suppress_restored_backlog_and_keep_disabled",
        "operator": "<name>", "decided_at": "2026-10-09T10:30:00+00:00", "reason": "receiver epoch reset agreed"}
     ]
   }
   ```

   | Disposition | Allowed for | Result |
   | --- | --- | --- |
   | `confirmed_completed` | jobs, runs, notifications, windows, experiment weeks | Needs an evidence file next to the decision file and its SHA-256 (a notification also needs `evidence.sent_at` from the receiver's record). Job `done`; run stays `failed` with `external_completed_result_unavailable` (a completed paid call is not a completed run; nothing is synthesized); notification `sent` with that `sent_at`. |
   | `suppress` | the same | Explicit abandonment. Job `done`, run and notification stay `failed`, marker `recovery:<id>:suppressed`. The ordinary retry route refuses the notification for good. |
   | `authorize_replay` | the same | Needs `"accepts_duplicate": true`: a replay may charge or deliver twice. Job `done`, run stays `failed` (start a new run through the API after release; it gets a new id); the notification goes back to `pending` with attempts 0, due at `release_at`, through the ordinary lease and fence path. |
   | `keep_disabled` | schedules, evaluators, alert rules, digest settings, audit streams | The first reopening never restores a producer's previous value; re-enabling is separate, reviewed work. |
   | `suppress_restored_backlog_and_keep_disabled` | audit streams | Advances `delivered_seq` to the restored organization's head and records the abandoned range. It does not claim receipt. Sequence numbers may be reused by events written after reopening, so the receiver needs an epoch reset or a new destination before the stream is enabled again. |
   | `unresolved` | draft files only | `apply` rejects the whole file before publishing or changing anything; the quarantine stays as prepared. Never infer completion or abandonment from an empty log, a timeout or an empty lookup. |

   Reconciled jobs are `done` with no `outcome` (the outcome column's CHECK lists only the
   worker's values); their disposition is the `last_error` marker.

   ```bash
   spanlight restore-reconcile apply --manifest /srv/recovery/manifest.json \
     --decisions /srv/recovery/decisions.json --all-services-held
   ```

   `apply` validates the whole file first (binding, completeness, uniqueness, allowed
   dispositions, evidence digests), publishes the apply plan as `manifest.json.apply.json`, then
   under row locks compares every quarantine image, writes the final images and the `applied`
   marker in one transaction. Rerunning it with the same files changes nothing; different
   decisions after an apply are refused; a draft with any `unresolved` or missing decision is
   refused with the quarantine untouched, and you supply a complete file later.

9. **Check, right before reopening.** With the release settings in the operator process:

   ```bash
   export EXPERIMENT_SCHEDULES_ENABLED=false EVALS_ENABLED=false ALERTS_EVALUATION_ENABLED=false \
     WEEKLY_DIGEST_ENABLED=false DEMO_ENABLED=false BILLING_SOURCES_ENABLED=false \
     PROVIDER_STATUS_POLLING_ENABLED=false
   spanlight restore-reconcile check --manifest /srv/recovery/manifest.json \
     --decisions /srv/recovery/decisions.json --all-services-held
   ```

   It is read-only and exits 0 only when the database is in the `applied` phase for exactly
   these files, every final image matches, sealed values and audit heads still verify, no job or
   run is queued or running, no notification is pending except the authorized replays, every
   producer row is off and the settings above are all `false`. It prints the digests and a
   reopening checklist. A write to the target afterwards invalidates it; run it again. Exit 3
   means another recovery command holds the lock; wait and retry.

10. **Reopen, degraded on purpose.** Point the API and worker `DATABASE_URL` at the restored
    database with the app role and start them **with the same settings as in step 9**. The
    scheduler creates fresh housekeeping jobs (retention, rollups, cleanup); it cannot create
    alert evaluations, digests, demo traffic, billing fetches, provider polls or scheduled
    experiment runs while the settings are off, and the quarantined rows are terminal and never
    claimed. Watch the worker for the authorized replays, then reopen the API and gateway ingress
    once held upstream retries are drained or reconciled. New requests after reopening are new
    authorizations; the procedure does not make independently retried client requests
    exactly-once.

    Re-enabling schedules, evaluators, alert rules, digests and audit streams is separate,
    reviewed work: each needs an occurrence, lookback or receiver decision first. Resume the
    prune schedule only when the objects a recovery might still need are safe.

### Rolling back

- **Before `prepare` committed:** the target is a plain restored copy; drop it and start again,
  or keep it as evidence. The old database is untouched.
- **After `prepare`, before `apply`:** the quarantine is terminal state in the target only. To
  abandon, drop the target (and the manifest's independent copy stays as evidence); to redo, the
  same manifest resumes. You cannot "un-quarantine": restore again into another new database.
- **After `apply`:** the plan and marker are immutable. If the decisions were wrong, restore
  again into another new database with a new recovery; keep the old target and files as the
  record of what was decided.
- **After reopening:** stop everything again (step 1); the old database, if you kept it, is the
  state before the incident, not before the restore.

## Restoring on Docker Compose

`deploy/compose.yaml` fixes the database name: the API, the worker and the `migrate` service all
connect to the database `spanlight` on the bundled `postgres` service. So the restore goes into a
second database on the same server, and the two are then swapped by renaming them **before**
`prepare`, which binds the final name. The old database is kept under another name until you
have checked the restored one.

Run these from the repository root. If you start the stack with extra files (such as
`deploy/compose.minio.yaml` or `deploy/compose.replicas.yaml`), add the same `-f` options to the
function.

```bash
spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
```

1. **Stop everything that connects to the database**, and anything that would restart it:

   ```bash
   spl stop web api gateway worker
   ```

   Hold any external prune schedule and deploy hook too. Data written after this point would not
   be in the restored database anyway, and the rename in step 5 needs the database to be unused.

2. **List the backups** and pick a key. Supply recovery-only credentials from a protected
   operator environment file to a one-off deployment image on the Compose network:

   ```bash
   docker run --rm --network <compose-network> --env-file <recovery.env> <deployment-image> spanlight backup list
   ```

3. **Create an empty database** next to the live one, and note the time for the context file:

   ```bash
   spl exec postgres psql -U postgres -d postgres -c 'CREATE DATABASE spanlight_restored'
   ```

4. **Restore into it, with a receipt.** The URL uses the owner password from `deploy/.env`, read
   by the shell so it never appears on the command line, and reaches the container through `-e`.
   Mount a durable operator directory for the receipt and the files that follow. The image runs
   as its own unprivileged `app` user, which cannot write a directory owned by root, so create the
   directory for your operator account and run the one-off container as that account:

   ```bash
   export RESTORE_TARGET_URL="postgresql://postgres:$(sed -n 's/^POSTGRES_PASSWORD=//p' deploy/.env)@postgres:5432/spanlight_restored"
   sudo install -d -m 700 -o "$(id -u)" -g "$(id -g)" /srv/recovery
   docker run --rm --user "$(id -u):$(id -g)" --network <compose-network> --env-file <recovery.env> -e RESTORE_TARGET_URL \
     -v /srv/recovery:/recovery <deployment-image> \
     spanlight restore --key backups/2026/10/09/spanlight-20261009T030000Z.dump --receipt /recovery/receipt.json
   ```

   This assumes `POSTGRES_PASSWORD` has no characters that need escaping in a URL. If a step
   fails, the stack is still stopped and the live database is untouched: start it again with
   `spl start worker api web`.

5. **Swap the databases.** Keep the current one as `spanlight_before_restore`, then give the
   restored one the name the stack uses:

   ```bash
   spl exec postgres psql -U postgres -d postgres \
     -c 'ALTER DATABASE spanlight RENAME TO spanlight_before_restore' \
     -c 'ALTER DATABASE spanlight_restored RENAME TO spanlight'
   ```

   If the first rename fails with "database is being accessed by other users", something still
   holds a connection; find it with
   `SELECT pid, usename, application_name FROM pg_stat_activity WHERE datname = 'spanlight'`.
   The context file's `renamed_from` is `spanlight_restored`.

6. **Grant the app role and bring the schema up to date; do not start the stack.** The dump
   carries no privileges, so the existing `spanlight_app` role cannot read the restored database
   yet. The `migrate` service runs three steps as the owner: `spanlight migrate` upgrades an older
   backup to the schema of the release you run, `spanlight ensure-app-role` grants the role access,
   and `spanlight activate-aggregate-coverage --writers-drained --fresh-install
   --skip-if-projects-exist` prints that it skipped, because the restored database has projects
   and their coverage state comes from the backup. `ensure-app-role` sets the role's password to
   `APP_DB_PASSWORD` from `deploy/.env`, the value the stack already uses, so leave that variable
   unchanged.

   ```bash
   spl run --rm migrate
   ```

7. **Prepare, decide, apply, check** as in [Steps](#steps) 6 to 9, with
   `RESTORE_TARGET_URL` now naming `spanlight` and the same one-off image, `--user`, network,
   environment file and `/recovery` mount as in step 4, adding `-e CREDENTIALS_KEYS` and the release
   settings. Check `/health/ready` only after reopening.

8. **Reopen with the producers off.** Add the release settings to `deploy/.env`
   (`EXPERIMENT_SCHEDULES_ENABLED=false`, `EVALS_ENABLED=false`, `ALERTS_EVALUATION_ENABLED=false`,
   `WEEKLY_DIGEST_ENABLED=false`, `DEMO_ENABLED=false`, `BILLING_SOURCES_ENABLED=false`,
   `PROVIDER_STATUS_POLLING_ENABLED=false`), start the worker first and watch it, then the rest:

   ```bash
   spl up -d worker
   spl up -d
   ```

   Then check the result with the queries in [Verify](#verify), for example
   `spl exec postgres psql -U postgres -d spanlight -c 'SELECT max(started_at) FROM traces'`,
   and confirm that `/health/ready` answers 200.

9. **Remove the old database** once you no longer need it. Until then it is a complete copy of
   the data from before the restore, and it uses disk space:

   ```bash
   spl exec postgres psql -U postgres -d postgres -c 'DROP DATABASE spanlight_before_restore'
   ```

   To go back instead, stop the stack as in step 1, rename `spanlight` to another name and
   `spanlight_before_restore` back to `spanlight`, and start the stack with `spl up -d`. The
   recovery files for the abandoned attempt stay as its record.

## Verify

Compare the restored database with what you expect, using the owner URL:

```sql
-- Row counts of the main tables.
SELECT 'organizations' AS table_name, count(*) FROM organizations
UNION ALL SELECT 'projects', count(*) FROM projects
UNION ALL SELECT 'traces', count(*) FROM traces
UNION ALL SELECT 'spans', count(*) FROM spans;

-- The newest trace, to confirm the data is as recent as the backup.
SELECT max(started_at) FROM traces;

-- The schema version matches the release you run.
SELECT version_num FROM alembic_version;

-- The recovery marker: id, manifest digest and phase.
SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database();
```

`prepare` already verified the sealed values, the audit chains, RLS and the app role, and
`check` repeats the verification; their results are in the manifest's `verification` block. Open
the dashboard after reopening, sign in and open one trace to confirm that its spans, prompts and
completions load.

Rollup tables are derived data. If the dashboard's long-range charts look thin, the next
`rollup_hourly` runs rebuild the last 48 hours, and `spanlight rollups backfill` rebuilds older
ranges.

## What a backup leaves out

- Rate-limit buckets (an `UNLOGGED` table). They hold only short-lived counters and refill on
  their own; `prepare` confirms the restored table is empty.
- Anything outside the database: object storage contents other than backups, environment
  variables, and `CREDENTIALS_KEYS`. Keep `CREDENTIALS_KEYS` in your secret manager; encrypted
  values in a restored database cannot be read without it.
- Everything that happened after the dump's snapshot: that is the recovery window the context
  file's `windows` and the manifest's experiment weeks make you decide about.

## Owner steps before the hosted service relies on this

The local drill proves the mechanism, not the deployment. Before `spanlight.dev` counts this as
its recovery procedure:

1. Run the drill against a copy of the hosted database on the hosting provider, with the hosted
   object storage and `BACKUP_S3_SSE` as deployed, and record the `DRILL` numbers, the database
   size and the release in this file under a "hosted drill" heading.
2. Confirm where the external evidence for decisions comes from: the provider ledgers (OpenAI,
   Anthropic) for paid calls, the receivers' records for notifications and audit streams, and the
   independently retained audit heads (`spanlight audit-verify` output stored outside the
   database).
3. Record how a fresh target database is created on the provider and what evidence of its
   creation is retained.
4. Decide the recovery-point objective the hosted terms promise. A daily dump supports "up to a
   day plus failures"; a 15-minute objective needs managed point-in-time recovery and its own
   drill.

## Credential isolation and retention

Exports use `S3_*`. Backups require the independent `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`,
`BACKUP_S3_SECRET_KEY` trio and optional `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`,
`BACKUP_S3_FORCE_PATH_STYLE`. Partial triples fail startup; generic and ambient credentials are
never a fallback. Only the worker receives routine backup credentials and `BACKUP_DATABASE_URL`.
API, web, migrations and standalone gateway receive no backup credentials. `exports_configured`
continues to describe export storage only.

Provision separate principals with provider tooling:

| Principal | Backup permissions |
| --- | --- |
| API/export | Deny List/Get/Put/Delete, including supported version operations; preserve export CRUD |
| Worker uploader | PutObject and AbortMultipartUpload on `backups/*`; no List/Get/Delete |
| Recovery operator | ListBucket for `backups/`, GetObject on `backups/*`; no Put/Delete |
| Retention operator | ListBucket for `backups/`, DeleteObject on `backups/*`; no Get/Put/version deletion |

Validate these permissions directly against the target provider before rollout. The pinned local
MinIO aliases GetObjectVersion to GetObject: the recovery principal can read versions there;
explicitly denying GetObjectVersion also blocks current reads. It similarly aliases version listing.
Its retention policy therefore uses the provider-specific lowercase `s3:versionid` Null condition
to reject version deletion while allowing current-object deletion; verify this condition on your
provider instead of copying its spelling without validation. Do not treat local policy evidence
as proof of a hosted provider's IAM semantics. The CLI restores only the current accessible object
and accepts no version selector; the receipt's SHA-256 binds the bytes that were restored, not
the backup's origin. Upload permission allows same-key overwrites; it is not immutability.
With versioning, current-object deletion can create delete markers; version reclamation needs an
independently reviewed provider policy.

Run retention from a separate operator process, with its own protected environment file containing
only the List/Delete principal through the same `BACKUP_S3_*` interface:

```bash
docker run --rm --env-file <retention.env> <deployment-image> spanlight backup prune
docker run --rm --env-file <retention.env> <deployment-image> spanlight backup prune --apply
```

For local MinIO add `--network <compose-network>` and use `http://minio:9000`. Never run retention
inside the worker: its upload principal must fail listing/deletion. Preview lists candidate keys
without writing. Apply obtains a fresh listing and deletes only its selected keys; listing or any
delete failure exits nonzero and reports counts. A concurrent upload to a distinct key survives.
Do not run prune concurrently with recovery of an object it might select.

Selection preserves the newest backup per each of the last 14 UTC days (today included), newest
backup per each of the last eight Sundays, newest overall, future-dated keys and unknown keys.
Do not replace this with a generic 56-day expiration. Schedule `backup prune --apply` daily after
backup completion using an existing host/provider scheduler; alert on failure and missed runs.
Pause it during migration/recovery. Without this separate schedule uploads continue and storage
grows. The incomplete-multipart cleanup lifecycle is independent and does not remove completed
objects. A network failure after a successful PUT may leave an orphan for later retention.

### Existing deployment migration

1. Inventory backup bucket/prefix, historical keyring, last recoverable backup and export access.
   Keep all existing objects and read access; do not automatically copy/delete legacy backups.
2. Provision distinct upload, recovery and retention principals. Prepare a paused daily retention
   schedule. Stage explicit backup settings on workers before deploying this no-fallback image.
3. Take and verify a new backup by restoring it into a newly created disposable database and
   running `restore-reconcile prepare` against it (it verifies key decryption, tenant RLS, audit
   integrity, the schema revision and the app role). Record object key, release, size, snapshot
   bounds and drill results before revoking any legacy backup permissions.
4. Only after that verification, revoke legacy API/export backup access and rotate shared/root
   credentials. Verify direct denials and working exports. Preserve recovery access to old buckets
   and every historical `CREDENTIALS_KEYS` entry for the lifetime of those backups.
5. Preview retention against the explicit new destination, then enable its independent daily
   schedule. Keep legacy-destination retention paused until its own bucket and preview are reviewed.

An old image uses generic `S3_*` for backups and cannot transparently roll back after revocation.
Use the reviewed new image/configuration or explicitly reauthorize the old principal as a security
rollback. Never reintroduce hidden fallback. Local MinIO initialization now creates distinct users;
existing root credentials must remain available only to operators until migration verification is done.
