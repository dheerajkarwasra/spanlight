/**
 * Why the per-user figures lag: the Users pages read rollups that a worker refreshes on a
 * schedule. The freshness line itself is `lib/freshness.ts`.
 */

/** Why the figures lag: the refresh schedule and the late-arrival cut-off (spec §9.6). */
export const COMPUTED_AT_EXPLANATION =
  "Per-user figures are recomputed every 15 minutes for the last three days and nightly for the last eight; a trace that arrives later than that is not counted.";

/** Shown when no day of the window has been computed yet. */
export const NOT_COMPUTED_EXPLANATION =
  "Not computed yet. Per-user figures are recomputed every 15 minutes; the first pass for this window has not run.";
