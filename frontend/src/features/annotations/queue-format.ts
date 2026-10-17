import type { AnnotationQueue, QueueItem, ReasonProgress, ScoreConfig, Span } from "@/lib/api";
import { formatInteger, shortId } from "@/lib/format";
import { allowedValuesText } from "@/lib/score-format";

/** A claim idle this long goes back to the queue (the backend's rule). */
export const CLAIM_TIMEOUT_MS = 30 * 60_000;

/** The most trace ids one add-items request takes. */
export const MAX_TRACE_IDS = 200;

function count(value: number): string {
  return formatInteger(value) ?? String(value);
}

/** The queue's configs in the queue's order; a config deleted since is left out. */
export function queueConfigs(
  queue: Pick<AnnotationQueue, "score_config_ids">,
  configs: readonly ScoreConfig[] | undefined,
): ScoreConfig[] {
  const byId = new Map((configs ?? []).map((config) => [config.id, config]));
  return queue.score_config_ids.flatMap((id) => byId.get(id) ?? []);
}

/** "relevance · 0 to 1", "format · yes or no", "tone · good, ok, bad". */
export function configSummary(config: ScoreConfig): string {
  return `${config.name} · ${allowedValuesText(config)}`;
}

export function progressTotal(progress: ReasonProgress): number {
  return progress.pending + progress.done + progress.skipped;
}

/** The share of the queue that is finished (done or skipped), 0–1. */
export function progressRatio(progress: ReasonProgress): number {
  const total = progressTotal(progress);
  return total === 0 ? 0 : (progress.done + progress.skipped) / total;
}

/** "24 of 60 done · 1 skipped", "All 12 done", "0 of 25 done". */
export function progressSummary(progress: ReasonProgress): string {
  const total = progressTotal(progress);
  if (total > 0 && progress.pending === 0 && progress.skipped === 0) {
    return `All ${count(total)} done`;
  }
  const skipped = progress.skipped > 0 ? ` · ${count(progress.skipped)} skipped` : "";
  return `${count(progress.done)} of ${count(total)} done${skipped}`;
}

export type ItemStatusTone = "done" | "mine" | "neutral";

/**
 * How an item reads in the table. A pending item claimed by someone else is "In review" until
 * its claim is 30 minutes old; after that anyone may claim it, so it reads "Pending" again.
 */
export function itemStatus(
  item: QueueItem,
  myUserId: string,
  now: number = Date.now(),
): { label: string; tone: ItemStatusTone } {
  if (item.status === "done") {
    return { label: "Done", tone: "done" };
  }
  if (item.status === "skipped") {
    return { label: "Skipped", tone: "neutral" };
  }
  if (item.assigned_to === myUserId) {
    return { label: "Assigned to you", tone: "mine" };
  }
  const claimedAt = item.assigned_at === null ? Number.NaN : Date.parse(item.assigned_at);
  if (item.assigned_to !== null && now - claimedAt < CLAIM_TIMEOUT_MS) {
    return { label: "In review", tone: "neutral" };
  }
  return { label: "Pending", tone: "neutral" };
}

/** The person behind an item: who finished it, else who holds it. */
export function reviewerOf(item: QueueItem): string | null {
  return item.completed_by ?? item.assigned_to;
}

/** "Whole trace", or the one span the item asks about. */
export function reviewingText(item: Pick<QueueItem, "span_id">): string {
  return item.span_id === null ? "Whole trace" : `Span ${shortId(item.span_id, 12)}`;
}

/** The latest thing that happened to the item. */
export function itemUpdatedAt(item: QueueItem): string {
  return item.completed_at ?? item.assigned_at ?? item.created_at;
}

/**
 * The span whose input and output the reviewer reads: the item's span, else the first LLM call,
 * else the root span. Null for a trace with no spans (or a span id it does not have).
 */
export function reviewedSpan(spans: readonly Span[], spanId: string | null): Span | null {
  if (spanId !== null) {
    return spans.find((span) => span.span_id === spanId) ?? null;
  }
  const byStart = [...spans].sort((a, b) => a.started_at.localeCompare(b.started_at));
  return (
    byStart.find((span) => span.kind === "llm") ??
    byStart.find((span) => span.parent_span_id === null) ??
    byStart[0] ??
    null
  );
}

const TRACE_ID = /^[0-9a-f]{32}$/;

/**
 * Trace ids pasted one per line (commas and spaces work too), trimmed, lower-cased and
 * de-duplicated. A token that cannot be a trace id (32 hex characters) is `malformed`.
 */
export function parseTraceIds(text: string): { ids: string[]; malformed: string[] } {
  const tokens = [
    ...new Set(
      text
        .split(/[\s,]+/)
        .map((token) => token.trim().toLowerCase())
        .filter((token) => token !== ""),
    ),
  ];
  return {
    ids: tokens.filter((token) => TRACE_ID.test(token) && /[1-9a-f]/.test(token)),
    malformed: tokens.filter((token) => !TRACE_ID.test(token) || !/[1-9a-f]/.test(token)),
  };
}
