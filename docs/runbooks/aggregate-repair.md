# Aggregate correction repair

Supported ingestion and usage-backfill corrections record all old and new trace/span UTC hours in the source transaction. The `repair_aggregates` job runs every five minutes on the existing leased queue. `AGGREGATE_REPAIR_ENABLED=false` is the safe code and deployment default. The job still reports backlog when consumption is off. Capture, source-loss protection, and pending reads remain active.

## Cutover

1. Apply expand-only migration 1028 before code that reads or writes dirty markers. Preserve the 1027 coverage activation and evidence. Migration does not activate coverage or capture historical corrections.
2. Drain old ingestion and usage-backfill writers before replacing them. Old writers must not accept unmarked changes after cutover. Deploy both reviewed capture and consumer checkpoints together for normal service.
3. Shadow compare known complete-source buckets against source aggregates before enabling consumption. Check `complete_ranges` for hours and `complete_day_ranges` for days under `lock_aggregate_sources`; compare only those complete ranges. Include old/new model, environment, user, release and promoted metadata groups, empty departed groups, and cost/token totals. Never relabel legacy or source-lost history complete. Use the existing guarded manual backfill only for identified complete-source historical gaps; there is no automatic pre-cutover invalidation backfill.
4. Explicitly set `AGGREGATE_REPAIR_ENABLED=true` on workers using the deployment configuration. In Compose, set it in the deployment environment. On Railway, set the worker variable (`deploy/railway/railway.ts` keeps it with `preserve()`), then review/apply and redeploy using the normal procedure.
5. Verify hourly and cost obligations converge, plus each enabled optional product. Inspect pending status and oldest-pending timestamps through the existing API. Materialized alert/digest inputs must be complete before declaring normal observations restored. Current authoritative raw reads have mask 0 and remain usable when their sources are complete.

Capture-on/repair-off is deliberately degraded service. Ordinary scheduled refreshes do not acknowledge dirty obligations. New affected materialized observations, alerts and digests remain pending until their relevant obligations settle. Recent-looking values alone do not prove restoration.

## Sharded obligations: release 1 (migration 1040)

Apply migration 1040 before deploying this code, then run `spanlight ensure-app-role` as usual.
It creates the empty `aggregate_dirty_shards` table, oldest-age index and forced tenant RLS
policies in one transaction with a five-second lock timeout. Existing rows and the shared
`aggregate_dirty_generation_seq` are unchanged. Release 1 still writes `aggregate_dirty_hours`;
`WRITE_SHARDS = False` keeps the release-2 writer disabled.

Before enabling sharded writes, verify every API, gateway and worker process runs release 1 or later. No release-0 reader or repair process may remain. When skipping release 1, stop and drain all API and worker instances before running `spanlight migrate`, run `spanlight ensure-app-role`, then start release 2. Rollback to release 1 is supported. Never return to release 0 while shard rows remain.

Readers and repair use both tables. A pair is runnable if any row is runnable, including a
zero-mask row. Discovery offers each project/hour pair once per pass, at its oldest runnable row's age. Backlog counts distinct pairs and uses the oldest retained row's age, including disabled-only and zero-mask rows. Repair rebuilds
an hour once for the union of its consumer masks, then settles each observed row under its own
generation. Concurrent new rows and changed generations remain pending. Reader pending age
uses only rows carrying a relevant consumer; pair ages may advance as older rows settle.

A table drains only when every consumer is enabled or its obligations are settled as unavailable
from durable source-loss evidence. Disabled attribution/user-daily bits can retain rows forever.
Do not delete these rows to force a drain. The old table remains part of the reader contract.

To downgrade 1040 after any sharded writes, first run release-1 code (writes old, reads and
repairs both), enable repair and every consumer needed to drain `aggregate_dirty_shards`, and
verify it is empty. Then roll back all readers/repair processes to pre-1040 code (release 0),
then downgrade 1040. The downgrade takes `ACCESS EXCLUSIVE` on the new table, sets the RLS
bypass, refuses any remaining row (including zero-mask rows), and drops only that table in the
same transaction. Never drop it while release-1 code still reads it. The generation sequence
and old-table obligations are preserved.

## Limits and monitoring

Each run attempts at most 100 distinct project/hour markers. It starts no new marker after 30 seconds, gives each marker transaction at most 5 seconds, and uses a 40-second outer cancellation deadline. PostgreSQL statements have a 5-second timeout and lock waits a 250 ms timeout. Cancellation and connection cleanup are cooperative, not an operating-system hard kill; an unresponsive driver or event loop may exceed wall-clock deadlines. The queue's existing lease, fencing and worker timeout remain in force.

The oldest-first keyset scan serves one project per round. A failed project is skipped for the rest of that run. Disabled-only complete-source work is filtered before the candidate limit, retains its age, and becomes eligible automatically when its product is re-enabled. Durable unavailable hour/day evidence settles the matching obligations even if a product is disabled. No snapshot is rebuilt from incomplete sources. Repaired effects and generation-checked bit completion commit in the same transaction. A concurrent correction resets all obligations with a fresh token; stale work cannot acknowledge it.

Worker metrics use no tenant labels:

- `spanlight_aggregate_pending_markers`: all retained project/hour pairs across both tables, including disabled work.
- `spanlight_aggregate_oldest_pending_seconds`: oldest retained age, including disabled work.
- `spanlight_aggregate_runnable_markers`: project/hour pairs eligible with current product flags, even when consumption is off.
- `spanlight_aggregate_repair_outcomes_total{outcome="repaired|unavailable|deferred|error"}`: committed classifications and failed attempts. A marker can contribute both repaired and unavailable, or repaired and deferred. A generation race is deferred.

Backlog gauges are snapshots at the end of successful observation passes; a timeout or outage may leave the last snapshot stale. Check worker liveness and `aggregate_repair_failed` / `aggregate_repair_hard_stop` logs too. The isolated normal-load repair target is 15 minutes, not a guarantee during overload, outage or disabled consumption. Persistent failures can delay a project; oldest age must remain visible. This job does not implement restart-fair recent-window scheduling or durable daily-period continuation.

## Rollback and downgrade

Disable consumption with `AGGREGATE_REPAIR_ENABLED=false` while capture and pending metadata continue. This deliberately re-enters degraded materialized observation service. Enable again and verify relevant backlog convergence before restoring normal observations. Disabled attribution/user obligations stay pending only for their own materialized products and do not prevent hourly/cost restoration.

Rolling capture code back creates an untracked correction interval. It requires writer drain and reconciliation before resuming and is not a routine rollback. Downgrading 1028 requires all marker writers and consumers stopped and **no dirty rows**, including zero-mask remnants. The migration refuses otherwise. Never reset/reuse the generation sequence while an old consumer may exist. Do not remove 1027 evidence, alter project coverage boundaries, delete financial history, or discard old markers based on retention age.

## Scheduled daily work and admission (5B1/5C1)

Scheduled attribution has its own durable progress in `aggregate_job_progress`.
It is independent of dirty repair: `AGGREGATE_REPAIR_ENABLED` does not control it,
and scheduled work never acknowledges dirty generations or consumer bits.
`ATTRIBUTION_ENABLED=false` pauses both queued daily handlers and recent attribution;
it preserves admitted obligations. User statistics retain their independent switch.

Revision 1029 admits both `attribution_daily` and `rollup_recent` for every existing
project in one locked transaction, then admits both for each new project in its
creation transaction. The immutable admission epoch is scheduling scope, never
source-completeness evidence. The invoker trigger uses ordinary grants, binds the
new project locally, and explicitly restores the caller's project setting. PostgreSQL 17
represents an initially absent custom GUC as an empty placeholder afterward; both
mean no project under `NULLIF` RLS policies. Nonempty session/local bindings are
restored exactly; an error rolls the entire insertion back. Provision the application
role's table, sequence and explicit function grants using `ensure-app-role` before
reopening project creation. No trigger uses `SECURITY DEFINER` or sets the bypass.

Every 15 minutes, `schedule_attribution_daily` receives the scheduler's UTC date
as an immutable horizon. It selects the oldest admitted unsettled period, even
when a newer dispatcher executes first. Each dispatched child refreshes exactly
one of the six dates `[period-7, period-2]`. Output and next-day progress commit
together under the current job fence. A stale/replayed child cannot rewind work.
Terminal, exhausted or cleaned-up child jobs allow a fresh dispatch number;
queued/running jobs retain their key until queue lifecycle handling settles them.
Six successful dispatch opportunities finish an ordinary period; there is no
15-minute daily completion promise. The separate recent job keeps yesterday/today
and the existing seven-day bootstrap for newly promoted metadata keys.

Daily scheduling outcomes are refreshed=1, unavailable=2 and expired=4, combined
as bits for a period. A complete empty day is refreshed. Source-unavailable days
preserve snapshots. A whole day expires only when its following midnight is at
or before database time minus `min(retention_days,90)` days. A partial retained
day still requires complete source coverage. Entire expired idle periods are
settled arithmetically; active periods retain their fixed remaining dates.
Increasing retention never resurrects discarded scheduling periods. These
outcomes do not certify source completeness or clear unrelated pending reads.

`attribution_daily_scheduled` reports attempted/dispatched/deferred/error counts,
elapsed time, and a bounded pending sample with its oldest observed due period.
This is a sample of attempted candidates, not global backlog depth; locked or
unvisited rows can be absent. Discovery failures are logged separately and leave
prior commits intact. `attribution_daily_expiration_staged` records a proposed
expired range inside its transaction; the progress row is authoritative if the
transaction later fails. `attribution_daily_settled` reports committed child outcomes.

The measured daily limits are a 30-second start deadline, 40-second outer
cooperative deadline, five-second domain/day transaction, one-second discovery
and heartbeat caps, 250ms lock waits, and at most 100 candidates per invocation.
Every phase shares the entry clock. A committed attempt may defer source work if
it consumes the start allowance. Lease loss stops the handler; an ordinary tenant
failure preserves its cursor and permits the next candidate. The existing
50-second worker limit remains the final cancellation bound. These are cooperative
limits, not an OS hard-kill guarantee or a tenant completion SLA. Local PostgreSQL 17
calibration covered 100 dispatches and single-day 5,000/50,000-trace promoted-key
work; larger tenants and sustained enrollment require separate capacity evidence.
The one-time migration uses five-second lock and 120-second statement abort ceilings;
the seed was measured with 10,000 projects. Review production size and lock activity.

**No intermediate deployment or worker resumption is supported between 5B and 5C.**
Drain old daily/recent workers before admission, install the new daily handlers AND
5C closed-hour execution/recovery/read gates, then resume compatible workers. Shared
schema alone is not a complete recent scheduling/read cutover. API ingestion may
continue only with the accepted source-loss/dirty-capture implementation. The
`rollup_recent` cursor and the read frontier must be managed by the matching 5C1 code.
Do not run a mixture of the old global recent handler and durable recent handlers.

Operational rollback pauses jobs and preserves progress, source coverage, dirty
capture and read gates. A schema downgrade requires stopped/drained schedulers,
daily/recent workers and recovery writers, plus paused project admission. It takes
one database instant T and refuses while daily work is active or settled before
UTC date T, any admitted recent cursor precedes `floor_hour(T - 10 minutes)`, required
admission is missing, or queued/running daily/recent jobs remain. Current recent
`computed_at` cannot replace this cursor proof. Terminal references and completed
attempt history alone do not block removal. The downgrade preserves 1027/1028
source/dirty evidence. Use the supported recovery below for unfinished recent work;
never manually advance a cursor to force a downgrade. Reinstallation establishes
a new admission epoch; preserve prior completed-history evidence separately when
needed for audit.


## Recent closed-hour work and recovery (5C1)

The registered `rollup_hourly` job commits one durable attempt before each project's
source work. Selection orders attempts durably across invocations, includes idle projects,
and skips locked progress rows. Every visited project refreshes the current three UTC hours
and examines up to three hours of its oldest admitted closed prefix in one transaction.
The fixed invocation target is `floor_hour(now - 10 minutes)`. Complete empty hours settle
without fabricated rows; unavailable hours settle their scheduling obligation while retaining
snapshots and permanent source-loss evidence. Retention changes never jump this cursor.
The source lock precedes progress and the live job fence is acquired last through commit.
A failed transaction preserves its cursor, outputs and dirty bits; its earlier attempt remains.

The same-statement read gate adds scheduling pending only for HOURLY materialized intervals
that intersect the admitted unfinished closed suffix. Unavailable status takes precedence
over pending, then complete. Other consumer masks retain their meanings. Raw current alerts
remain available when their source coverage is complete. The hourly/raw frontier is capped
by the durable cursor, while the existing 24-hour raw-tail bound remains; a bound that forces
unfinished materialization produces pending metadata. Missing expected admission is an error,
not an empty/complete result. Neither current `computed_at` nor a settled scheduling cursor
clears independent dirty obligations or certifies unavailable sources.

Recent candidate starts use the first 20 seconds of the shared 30-second start allowance.
The 40-second cooperative outer deadline, five-second domain transactions, one-second
discovery/heartbeat caps, 250ms lock waits and 100-candidate limit match daily scheduling.
Maintenance uses only visited projects: at most one six-hour refresh slice and one 24-hour
gap chunk per project. Score-flag repair shares the same remaining start/deadline allowance.
No later phase receives a fresh clock. Logs expose committed visited-project observations,
not global backlog depth; unvisited, locked and failed projects can be absent from the sample.

For an explicitly selected project, inspect its `rollup_recent.next_closed_hour` and run:

```sh
spanlight rollups backfill --project PROJECT_UUID --from CURSOR_UTC --to END_UTC --resume-recent
```

Endpoints must be exact whole UTC hours. `--from` must equal the current durable cursor;
`--to` cannot exceed the fixed finalization target, and one request cannot exceed 90 days.
The command commits at most 100 three-hour chunks and starts none after 30 seconds. Its
40-second cooperative envelope includes session rollback/close and owned engine disposal.
An interruption or limit leaves the exact committed prefix for the next invocation. A stale
start reports the actual cursor; a later island is rejected. Cursor validation repeats under
the source/progress locks, so a concurrent worker cannot cause recovery to replay stale work.
Use the reported cursor for the next request. Without `--resume-recent`, manual backfill
retains its existing widening behavior and does not settle scheduled cursor obligations.

After recovery, settle daily periods using the registered daily dispatcher/children, drain
queued/running jobs and stop admission/schedulers/writers before the guarded downgrade.
The guard rechecks its own database instant; a newly due hour/day may require another recovery
pass. Preserve 1026 financial and 1027/1028 source/dirty evidence throughout. Recovery is not
permission to delete evidence, enable dirty repair, or declare normal observations restored.

Local PostgreSQL 17 calibration includes six disjoint hours with 60,000 spans and 6,000 error
groups. Broader-sequence testing exposed a stale-statistics plan that repeatedly scanned
all traces for a newly busy tenant. The error aggregate now uses an inner LATERAL primary-key
lookup with `OFFSET 0` to retain parameterization and inner-join/tenant/NULL semantics. Paired
30,000-error insert measurements changed a diagnostic ten-second timeout to approximately
112ms without new indexes, planner settings or ANALYZE in the job. This is a measured local
remedy, not an arbitrary tenant-size SLA. Larger workloads still require capacity measurement;
timeouts retain truthful pending state. All deadlines remain cooperative, not OS hard kills.
