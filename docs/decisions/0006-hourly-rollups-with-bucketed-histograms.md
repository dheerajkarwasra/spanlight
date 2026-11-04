# 6. Long-window metrics read hourly rollups with bucketed latency histograms

Date: 2026-10-09 · Status: accepted

## Context

The metric endpoints (overview, time series, per-model table) aggregate spans: counts, errors, tokens, cost and latency percentiles. Reading the raw `spans` table is exact, and for a short window it is fast, because the project's rows for a day are few and an index covers the time range.

The cost grows with the number of rows in the window, not with the number of numbers on the screen. A 30-day chart over a busy project reads millions of spans to draw a few hundred points, and `percentile_cont` has to sort all the durations in each group to answer. Postgres [is the only datastore](0001-postgres-only-storage.md), so the answer cannot be a columnar store or a separate time-series database. It has to be a smaller table in the same database.

Counts, sums and error totals can be pre-aggregated and added back together without loss. Percentiles cannot: the p95 of two hours is not a function of the two hourly p95 values, and keeping every duration defeats the purpose. A rollup has to keep something from which a percentile can be rebuilt.

## Decision

Maintain two **hourly rollup tables** and read them for long windows.

- **Tables.** `span_rollups_hourly` has one row per project, hour, environment, provider, model and span kind. It holds the span count, errors, input, output and cached tokens, the summed cost, the number of unpriced calls, and two latency histograms. `trace_rollups_hourly` has one row per project, hour and environment, with the trace count and errored trace count. Unknown dimensions are stored as `NULL`, never as an empty string, and uniqueness uses `UNIQUE NULLS NOT DISTINCT` so that `NULL` counts as one value. Both tables have the same row-level security policy pair as `spans`.
- **Histograms.** Each latency histogram is an `int[32]`. The 32 upper bounds are log-spaced from 1 ms to 300 000 ms, each about 1.5 times the previous one, and a duration above the last bound is counted in the last bucket. One histogram is for span duration and one for time to first token. A duration belongs to the first bucket whose bound is at least as large, so its index is the number of bounds below it. Merging two hours is an element-wise sum. A percentile is found by walking the cumulative counts to the bucket that holds the rank and interpolating linearly inside it.
- **Cost.** The summed cost is `NULL` only when every span in the row is unpriced, the same rule the raw query follows, and `unpriced_calls` counts the rest. Unknown stays unknown.
- **No session counts.** Distinct sessions do not add across hours, so the rollups carry no session column.
- **Which source answers.** A window of 24 hours or less reads the raw spans and returns exact percentiles. A longer window reads the rollup hours and its response is marked `approximate`. The previous period used for comparison follows the same rule as the current one, so the two are never computed differently.
- **Maintenance.** A worker job runs every 5 minutes and recomputes the last 48 hours for every project that has spans in that range. For one project and an hour-aligned range, the job deletes the rollup rows and inserts fresh aggregates in a single transaction. It deletes first instead of upserting, so a group that no longer exists disappears: a re-sent span that changes its model or environment would otherwise leave a stale row behind. Spans are placed in the hour of their `started_at`, and their environment is the one on their trace. A project that has spans but no rollup rows is backfilled over its retention window the first time the job sees it, and a command can backfill any range by hand.

The bucket arithmetic lives in one small module with no database access, and the SQL that fills the histograms takes its bounds from the same constants, so the two cannot disagree about which bucket a duration is in.

## Consequences

- **Percentiles over long windows are approximate.** A bucket is about 1.5 times wide, so an estimate can be off by roughly 25 %. That is invisible on a chart spanning weeks, and it is why the API says `approximate` and the UI says so, instead of showing a rollup number as if it were exact. The last bucket is open-ended, so a p99 above 300 s is reported as 300 s.
- **Long-window reads are cheap.** A query reads at most 24 rows per dimension combination per day instead of every span, and its cost no longer depends on request volume.
- **Rollups lag by up to 5 minutes.** A window over 24 hours can miss the newest spans until the next run. The UI tooltip says so, and windows of 24 hours or less are not affected.
- **Late arrivals have a limit.** A span that arrives more than 48 hours after its `started_at` is outside the recompute window and is not reflected until that range is backfilled by hand. SDK exports and collector retries arrive far sooner than that, so this is documented, not engineered around.
- **Editing the buckets is a migration.** The bounds are baked into stored arrays. Changing them means recomputing every rollup row from the raw spans, which is possible only while the spans are still within retention.
- **The write path does not change.** Ingestion stays [synchronous and idempotent](0004-synchronous-idempotent-ingestion.md), and rollups are derived data that can be rebuilt at any time from the spans.
- **Plain Postgres has a ceiling too.** If the rollup tables themselves grow too large, partitioning them by month is the next step, taken when measurements show it is needed.

## 2026-11-03 accuracy amendment

The original within-bucket uniform interpolation and approximate 25% claim above are superseded. The old `q * n` method could estimate the median of `[2, 2000]` ms as 2.256 ms instead of PostgreSQL's exact 1001 ms. An open-ended bucket does not preserve the actual duration, and an overflow percentile was never guaranteed to equal 300 s.

The shared estimator now follows the continuous rank `q * (n - 1)`. Each contributing order statistic uses its bucket's harmonic midpoint, `2LU / (L + U)`, which minimizes worst-case relative error within a bounded positive interval `[L, U]`. Interpolate those two representatives with the fractional rank, preserving monotonic percentiles, including across the first bucket. The first bucket uses its absolute-error midpoint, 0.5 ms. Integer-ratio rank arithmetic avoids losing ranks when counts exceed `2**53`.

For bounded interior buckets, the edge ratio is about 1.50204 and the relative error bound is `(ratio - 1) / (ratio + 1)`, about 20.066%. Interpolating representative values preserves this bound because each observation's absolute error is bounded by that fraction of its value. This is a worst-case bound, not a promise that every estimate improves. It excludes the first bucket (no positive lower bound) and final bucket (open-ended); estimates cannot exceed 300 s. Raw-only reads remain exact.

This is a read-time correction: boundaries, stored histograms, exact sums, tenancy, and API shapes stay unchanged, so existing rollups need no migration or backfill. Estimated latency and TTFT alerts near thresholds can change; Doctor latency baselines also use this estimator. Historical benchmark figures retain their original values; new measurements must identify their source revision. A tighter error target would need separately versioned denser buckets or a mergeable sketch and a backfill decision.

[PostgreSQL's continuous percentile semantics](https://www.postgresql.org/docs/17/functions-aggregate.html) define the rank behavior. [Datadog's DDSketch paper](https://arxiv.org/abs/1908.10693) motivates relative-error evaluation; this correction uses Spanlight's existing histograms and does not implement DDSketch.
