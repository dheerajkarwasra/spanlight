# Retention

The `retention` job runs every hour on the worker's leased queue. It deletes what is past its retention in fourteen cleanup classes, one bounded batch per opportunity, rotating over the classes and over the projects (or billing sources) inside each class. Nothing drains in one run: a project with a large backlog loses `TRACE_BATCH` traces per visit and waits for its next turn, so a locked or backlogged project cannot keep the other projects or the later classes from being served.

## What the job does

Each run serves up to 448 opportunities and starts no new one after 30 seconds; the whole run, including rollback and connection cleanup, is cancelled at 40 seconds, inside the worker's 50-second short-job allowance. An opportunity is:

1. **Discovery.** One short transaction on `retention_progress`: lock the class with the lowest `attempt_order` (`SKIP LOCKED`: a class another worker holds is skipped, not waited for), pick the next scope of its roster by UUID (`id > after_scope_id`, or the first scope again past the last one), move the class to the end of the order, check the job's lease is still live, commit. An empty roster still rotates. The two global classes have no scope.
2. **Work.** One transaction of at most 10 seconds (PostgreSQL `statement_timeout` 10 s, `lock_timeout` 250 ms): set the worker bypass, lock the project (`FOR KEY SHARE`, plus the aggregate source lock where derived data or source-loss evidence is involved), re-read its `retention_days`, run one bounded statement, check the job's lease is still live, commit. A policy extension committed before that read is honoured.

Lease loss ends the run; any other failure rolls that batch back, keeps the opportunity spent and lets the run go on.

| Class | Roster | One batch | Cutoff |
| --- | --- | --- | --- |
| `trace_cascades` | projects | up to 1 000 traces with all their spans; the hours they touch are marked as lost to the aggregates first, in the same transaction | trace `started_at` before `now - retention_days` |
| `hourly_aggregates` | projects | up to 5 000 rows from each of the four hourly tables (span, trace and error rollups, telemetry losses), oldest first | whole hours before the cutoff's hour |
| `resolved_insights`, `detector_runs`, `scores`, `online_evaluator_runs`, `annotation_queue_items` | projects | up to 5 000 rows | project cutoff (detector runs: 7 days; online runs: also before the current month) |
| `user_stats`, `daily_cost`, `daily_attribution` | projects | up to 5 000 rows, under the aggregate source lock | whole UTC days before the cutoff |
| `provider_cost_lines`, `fetch_runs` | billing sources | up to 5 000 rows; a run waits while a line still points at it | 400 days |
| `provider_incidents` | global | up to 5 000 incidents, oldest first | 730 days |
| `audit_events` | global | up to 5 000 events, oldest first; `AUDIT_RETENTION_DAYS=0` deletes nothing | `AUDIT_RETENTION_DAYS` |

Fairness, when no cursor row is held by another worker: every class gets an opportunity within 14 selections, 32 opportunities per run, and every (class, project) pair is visited within `ceil(projects / 32)` runs, across worker restarts (the cursor is in the database). With two or more workers the rotation stays durable but that count is not guaranteed. This is a bound on opportunities, not on deletion: a batch that times out keeps its turn spent and the project waits for the next wrap.

## Measured envelope

Local macOS, Docker Desktop, PostgreSQL 17 Testcontainer, ordinary application role, synthetic fixtures with 512-byte payloads per span, spans spread over 24 hours; host load averages 5.4 / 15.4 / 24.5 at the start (other work running). Not a production SLA.

| Shape | Batch | Deleted | Transaction | WAL |
| --- | --- | --- | --- | --- |
| 5 000 small traces, 4 spans each | 100 parents | 100 traces, 400 spans | 0.02 s | 39 KB |
| same | 1 000 parents | 1 000 traces, 4 000 spans | 0.05 s | 343 KB |
| same | 5 000 parents | 5 000 traces, 20 000 spans | 0.40 s | 17 MB |
| one trace with 1 000 spans | 1 parent | 1 000 spans | 0.01 s | 62 KB |
| one trace with 10 000 spans | 1 parent | 10 000 spans | 0.03 s | 568 KB |
| discovery, 64-project roster | 100 rotations | | 0.04 s | |
| empty opportunity (nothing expired) | 20 work transactions | | 0.06 s | |
| full run: 32 projects with 2 000 expired traces each, 32 quiet | 448 opportunities | 17 000 traces | 3.5 s | |

Supported single-trace shape: a trace of up to 10 000 narrow spans deletes in one opportunity with two orders of magnitude of margin under the 10-second bound. Wider payloads (up to 1 MiB per input or output) and traces beyond 10 000 spans were not measured; a trace that cannot delete inside 10 seconds times out on every visit and stays (`statement_timeout` outcome below). Report such a trace; do not raise the bound without measuring.

## Monitoring

Each run logs `retention_applied` with `opportunities`, `outcomes` (counts by outcome), `deleted` (rows by class), `incomplete` and `elapsed_s`. `incomplete` is true when the run saw work it did not reach: a batch came back full (more rows of that scope were eligible), a batch failed or timed out, or the run stopped early (start deadline, contended cursors, a discovery error). A quiet system logs `incomplete=false` every hour. A failed batch logs `retention_batch_failed` with the class and scope id (worker log only; scope ids never reach metrics).

`spanlight_retention_batches_total{cleanup_class, outcome}` counts opportunities by class and outcome:

- `deleted`, `empty`: committed work, or nothing expired for that scope.
- `no_scope`: the roster was empty, or the project was deleted between discovery and work.
- `lock_timeout`: ingestion or another job held the rows for more than 250 ms; the scope is retried on its next turn.
- `statement_timeout`, `timeout`: the batch outgrew its 10 seconds (the second is the cooperative cancellation); a scope that shows this every hour has a shape outside the measured envelope.
- `error`: anything else; the log line has the SQLSTATE or traceback.

A run that logs `contended` in `outcomes` found every cursor row locked by another worker at that moment and stopped; `discovery_timeout` means the cursor table could not be read, or the lease not extended, within a second; `discovery_error` is a database error there (the log line `retention_discovery_failed` carries the SQLSTATE), and the run stops cleanly with its rotation rolled back. Any of these, repeating, means the worker or the database is unhealthy, not that there is nothing to do. The job extends its lease once per round of the 14 classes, not before every opportunity: a deliberate deviation from the design brief, since the whole run ends inside the 60-second lease.

Incomplete runs are normal while a backlog drains. `incomplete=true` every hour together with growing `lock_timeout` or `statement_timeout` counts for one class is the signal to look at that class's tables.

## Cursor rows

`retention_progress` holds exactly one row per class. The job refuses to run (`retention_progress is missing cleanup classes`) rather than silently skip a class. To restore a missing row:

```sql
INSERT INTO retention_progress (class, attempt_order) VALUES ('<class>', 0);
```

Deleting the project a cursor points at is harmless (`after_scope_id` is a keyset boundary, not a foreign key). Resetting every cursor (`UPDATE retention_progress SET attempt_order = 0, after_scope_id = NULL`) restarts the rotation from the first project of every class and delays no evidence; it only reorders turns.

## Upgrade, rollback and downgrade

Migration 1035 creates the table, its sequence and the fourteen rows. Run `spanlight ensure-app-role` after `spanlight migrate`, as every deploy does: when the migration runs under a different owner than the one whose default privileges the application role inherited, that step grants the new table and sequence.

Stop the worker before migrating in either direction, so no run holds a cursor row. Rolling the code back to the previous retention job restores the previous behaviour: every project is drained in turn in one run with no bound on the run's length. Downgrading 1035 drops the table and the sequence and loses only the rotation order; it never touches source-loss, dirty or financial evidence. The next upgrade seeds fresh rows and the rotation starts over.
