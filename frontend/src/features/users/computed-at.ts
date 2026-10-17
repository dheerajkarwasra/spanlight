/**
 * When the per-user figures were last recomputed. The Users pages read rollups that a worker
 * refreshes on a schedule, so the figures lag ingestion; the label says by how much.
 */

/** Why the figures lag: the refresh schedule and the late-arrival cut-off (spec §9.6). */
export const COMPUTED_AT_EXPLANATION =
  "Per-user figures are recomputed every 15 minutes for the last three days and nightly for the last eight; a trace that arrives later than that is not counted.";

/** Shown when no day of the window has been computed yet. */
export const NOT_COMPUTED_EXPLANATION =
  "Not computed yet. Per-user figures are recomputed every 15 minutes; the first pass for this window has not run.";

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * "Updated 4 min ago", "Updated 2 h ago", "Updated 3 d ago" or "Updated just now" (under a
 * minute, or slightly in the future when the clocks disagree). Null when the figures were never
 * computed or the value is not a timestamp.
 */
export function computedAtLabel(iso: string | null, now: Date): string | null {
  if (iso === null) {
    return null;
  }
  const computedAt = new Date(iso).getTime();
  if (Number.isNaN(computedAt)) {
    return null;
  }
  const elapsed = now.getTime() - computedAt;
  if (elapsed < MINUTE_MS) {
    return "Updated just now";
  }
  if (elapsed < HOUR_MS) {
    return `Updated ${Math.floor(elapsed / MINUTE_MS)} min ago`;
  }
  if (elapsed < DAY_MS) {
    return `Updated ${Math.floor(elapsed / HOUR_MS)} h ago`;
  }
  return `Updated ${Math.floor(elapsed / DAY_MS)} d ago`;
}
