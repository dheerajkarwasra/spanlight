/**
 * How fresh rollup-backed figures are. The Users and Attribution pages read rollups that a worker
 * refreshes on a schedule, so the figures lag ingestion; the line says since when and how many
 * traces are not counted yet.
 */
import { formatDayTime } from "./clock-format";
import { formatInteger, pluralNoun } from "./format";

/** The server stops counting pending traces here, so the count is then a lower bound. */
const PENDING_CAP = 10_000;

/** "432 traces", "1 trace", "10,000+ traces": the cap makes the count a lower bound. */
export function pendingTracesNoun(pending: number): string {
  const count = `${formatInteger(pending) ?? String(pending)}${pending >= PENDING_CAP ? "+" : ""}`;
  return `${count} ${pluralNoun(pending, "trace")}`;
}

function pendingPhrase(pending: number, since: string): string {
  const verb = pending === 1 ? "isn't" : "aren't";
  return `${pendingTracesNoun(pending)} ${since} ${verb} included yet`;
}

/**
 * "Updated 14:05 · 37 traces since then aren't included yet" ("Updated yesterday 23:50",
 * "Updated Oct 18 09:00" before today), or "Not computed yet · 432 traces in this window aren't
 * included yet" when the window was never computed. The pending part is left out when nothing
 * is pending or the count is unknown.
 */
export function freshnessLabel(
  computedAt: string | null,
  pendingTraces: number | null | undefined,
  now: Date = new Date(),
): string {
  const when = computedAt === null ? null : formatDayTime(computedAt, now);
  const pending = pendingTraces ?? 0;
  if (when === null) {
    return pending > 0
      ? `Not computed yet · ${pendingPhrase(pending, "in this window")}`
      : "Not computed yet";
  }
  const updated = `Updated ${when.replace(/^Today /, "").replace(/^Yesterday/, "yesterday")}`;
  return pending > 0 ? `${updated} · ${pendingPhrase(pending, "since then")}` : updated;
}
