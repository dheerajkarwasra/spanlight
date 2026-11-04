# 17. Gateway budgets reserve before forwarding

Date: 2026-10-25 · Amended: 2026-11-01 · Status: accepted

## Context

Evaluated alert state alone lets concurrent calls pass a cap between evaluations. The original
`max(measured, settled) + reserved + estimate` rule also hid new charges below historical spend
when a budget was created or edited during its period. Settlement deleted charge identity, so
cached measurements could not be reconciled exactly with current counters.

## Decision

Before forwarding a priced call, lock matching budgets and their period counters in stable
budget order, then reserve its estimate against every matching enabled `block` budget. Commit
before contacting the provider. No database connection remains held during the provider call.

Admission uses a fresh, uncached SQL statement per budget after acquiring its counter lock:

1. Sum matching, non-usage-rollup spans within the UTC period, excluding identities represented
   by that budget's period reservations.
2. Add settled/reaped correlated calls once. Use their current stored cost when available, zero
   for a usage-rollup span, otherwise their retained final charge or reaped estimate. These
   calls remain assigned to their admission period, including settlement after a reset.
3. Add held reservations and the new call's estimate. All matching budgets must fit.

These terms are disjoint. Calls admitted before budget creation appear in the first term when
stored. Corrections to stored costs appear in the next admission snapshot. A stored span and
its settlement commit together; holding the counter prevents tracked settlement/reaping from
changing the accounting boundary during the check. Untracked SDK/OTLP ingestion remains
unblocked and can commit independently; newly committed history is read on the next admission.
Alert evaluation remains responsible for notifications and its existing firing-state refusal,
but its minute-rounded, cached measurements are never an accounting baseline.

`budget_reservations` retains `settled` identity and final `charged_usd`, in the existing table,
while its raw span exists and for at least 90 days from admission. It retains no request/response payload or scope
metadata. Settlement transitions `held`/`reaped` once; repeated settlement is a no-op. A reaper
counts only rows its outer UPDATE actually transitions from `held` to `reaped`. Late settlement
corrects the estimate by `charge - amount`. Period counters retain settled/reserved totals;
fresh admission reads correlation to account for current stored corrections.

Scope/period edits return `409 BUDGET_RESERVATIONS_PENDING` while calls are held/reaped, a
positive conservative settled charge has no priced span to reclassify, or relevant legacy
unattributed charges remain. Eligible edits retain settled identity. A scope edit marks existing rows `reclassified`: their
charge follows the new scope's current stored dimensions, including later dimension corrections;
missing raw rows retain a conservative fallback and coverage checks. New calls retain their
original admission-scope charge until a later scope edit. Period edits remap each retained row
from the immutable admission clock (`expires_at - 12 minutes`), never its corrected span time.
Only counter aggregates reset. The fixed lease is never renewed; changing it requires migrating
this admission-clock invariant. Pending/reaped/unknown and relevant legacy guards remain. Amount/name changes preserve charges and holds.

## Historical coverage and migration

Raw telemetry retention is unchanged. Enabled daily hard budgets require at least one day of
project retention; monthly budgets require 31 days. Creation/update validates this and admission
rechecks it. A statement-level, security-invoker DELETE trigger keeps every deleted
positive-cost, non-usage-rollup span of the current UTC month in `budget_deleted_spans`: ids,
start time, cost, model and gateway key, no user identifier, trace name, metadata or payload.
Admission counts those rows with stored history, so retention, an end-user erasure or an
operator's deletion neither hides spend nor blocks the project; a span stored again, or one a
reservation already charges, is counted once. Project- and key-scoped budgets count deleted
spend by key, model-scoped budgets by model; the trace-bound scopes (user, trace name, metadata)
count all of it, since the trace is gone (conservative). The worker prunes the ledger with the
counters after 62 days. Project deletion writes no ledger row and does not recreate the project.

Migration 1031 cannot recover history deleted before it. It marks `budget_history_missing_through`
only for projects with evidence of a deletion this month: retention short enough to have
deleted a span of this month, an `end_user.erase` or `end_user.erase_request` audit event, or a
`project.update` event that changed `retention_days`. A marked project's current-period hard-budget
calls fail closed until the next UTC period, subject to retention requirements. Nothing else sets
the marker.

Deployment must drain old gateway/worker writers before migration and switch every replica to the
new code. Mixed-version rolling writers are unsupported: the old settlement implementation
deletes the correlation the new accounting requires.

Existing settled counter money is preserved. The known reaped subtotal is separated, and the
remaining legacy amount is retained as an explicitly uncertain conservative carry. It may
already overlap stored history, causing over-refusal; it is never described as exact history.
Relevant scope edits cannot discard it. New period counters have no legacy carry. An operator
must not clear coverage/carry without external evidence. Downgrade retains counter money but
removes settled correlation and the ledger, restoring the old implementation's limitations; a
subsequent upgrade re-derives the marker from this month's evidence.

Amended 2026-11-01 (owner decision): deleted spend is kept and counted instead of failing closed;
the blanket migration marker is replaced by the evidence rule above.

Amended 2026-11-01 (owner decision "snapshots"): admission sums closed UTC hours from per-budget
snapshots (`budget_bucket_snapshots`, migration 1032) and scans only the open hour plus the
12-minute lease. A snapshot is valid while its generation equals the hour's
`budget_bucket_versions` row, which every span, trace-field and reservation change bumps in its
own transaction through statement triggers; a scope or period edit drops the budget's snapshots.
Each term keeps its row-local predicates; correlated calls are bucketed by the admission clock
`expires_at - 12 minutes`, so the literal in the trigger functions is part of the lease-clock
invariant. Nonoverlap, correction, deletion and late-write semantics are unchanged and proven
equal to the full period scan by `tests/budgets/test_snapshot_equality.py`; the full scan remains
the oracle (`scan_history`) and deleting snapshots the repair. A writer that bypasses the triggers
(`ALTER TABLE ... DISABLE TRIGGER`, a bulk `COPY` with triggers off) leaves stale snapshots until
that repair.

Amended 2026-11-01 (owner decision "running hour total"): every hour of the period is a row,
the open one included. Each hour's row is kept exact by writer deltas (`budget_bucket_deltas`,
migration 1033, compacted under the row lock) for the changes a writer can price exactly: new
or replayed uncorrelated spans, `held -> settled` charges; every other change (a deleted span,
a reaped or reclassified call, a trace-field change) bumps the hour's version and is
recomputed once. Admission cost no longer grows with the calls or spans of the open hour.

## Estimate and guarantee boundaries

The estimate prices text-bearing request fields and the maximum requested output-token limit
(times `n`), or 16,384 output tokens if absent, at the dearest priced requested/routed model and
service tier. Image, file, and audio parts are not counted. Oversized token counts clamp to the
largest priced count. Unpriced calls are admitted as unknown; a zero-cost estimate holds nothing.
A missing span/usage may be charged at its estimate when the provider may have billed; a call
never sent or rejected with an HTTP error status has no conservative charge.

The guarantee is reservation-based admission for supported priced estimates. Actual cost can
exceed an estimate, and direct-provider traffic is outside the gateway. This is **not an
unconditional provider-bill cap**. Ingestion cannot be retrospectively refused. A cache hit costs
no provider spend, so the cache is read before admission: a hit is served even when a `block`
budget fires, reserves nothing and is read-only (since 2026-11-02).

## Performance and operational consequences

This amendment intentionally replaces the no-per-call-span-sum objective with a bounded fresh
period scan plus retained correlation. Correctness takes priority over the old O(1) counter
claim. Existing project/time and identity indexes support the reads, but monthly/high-volume
budgets need a measured mixed-gateway load gate before performance acceptance. No latency or
throughput guarantee follows from this decision. Future snapshot optimization must prove the
same nonoverlap, correction, deletion, and late-write semantics first.

Since migration 1042 the common admission (every counter row present, no hour of the period
stale or missing) runs as one call to the database function `budget_reserve_fast`: the same
counter lock, hours read, refusal rule and hold, as separate statements inside the function so
each read still starts after the lock was granted. Any other case falls back to the same steps
issued one by one in the same transaction. The function runs with the caller's rights, so
row-level security applies unchanged.

Reservations expire after 12 minutes; the reaper runs every minute in bounded batches. Counter
rows are pruned after 62 days. Correlation is pruned only when no matching raw span exists and
90 days have elapsed since admission, independent of the earlier monthly period start. Existing
reservation/block/reaper metrics remain. Reintroducing a deleted natural identity after both raw
and accounting retention have expired is outside bounded deduplication; perpetual tombstones
are not retained. This does not extend raw retention or authorize additional payload storage.
