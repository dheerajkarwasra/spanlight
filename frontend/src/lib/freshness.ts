/**
 * How fresh rollup-backed figures are. The Users and Attribution pages read rollups that a worker
 * refreshes on a schedule, so the figures lag ingestion; the line says since when and how many
 * traces are not counted yet.
 */
import { formatDayTime } from "./clock-format";
import { docsLink } from "./docs-links";
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

/** Figma "Aggregate history" (806:302, 806:324): the tooltip and empty-state explanation. */
export const SOURCE_REMOVED =
  "Source rows were removed. Stored values are snapshots; missing values are unknown.";

/** Coverage never activated also reads as unavailable history: say who can fix it. */
export const SOURCE_HISTORY_HELP = `${SOURCE_REMOVED} Ask your administrator to activate aggregate coverage.`;

/** The deploy runbook's one-time coverage activation step. */
export const COVERAGE_DOCS = docsLink(
  "runbooks/deploy/#activate-aggregate-source-preservation-1027",
);

/** Figma "Aggregate history" (806:324): the label and title when nothing was retained. */
export const SOURCE_HISTORY_UNAVAILABLE = "Source history unavailable";

type AggregateStatus = "complete" | "pending" | "unavailable" | undefined;

/** Source history is unavailable and nothing was retained: the figures are unknown, not 0. */
export function sourceHistoryGone(status: AggregateStatus, hasFigures: boolean): boolean {
  return status === "unavailable" && !hasFigures;
}

/**
 * The label for figures whose source history is unavailable: "Retained snapshot" when the
 * response still carries stored figures, "Source history unavailable" when it carries none.
 * Null for complete, pending or legacy responses, which keep their usual freshness line.
 */
export function sourceHistoryLabel(status: AggregateStatus, hasFigures: boolean): string | null {
  if (status !== "unavailable") {
    return null;
  }
  return hasFigures ? "Retained snapshot" : SOURCE_HISTORY_UNAVAILABLE;
}
