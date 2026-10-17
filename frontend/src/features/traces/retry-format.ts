import type { TraceSort } from "@/lib/api";
import { formatCost } from "@/lib/format";
import { RETRY_NO_KNOWN_COST_REASON, RETRY_NOT_COMPUTED_REASON } from "@/lib/retry-reasons";

/*
 * How a trace's retries read: `retry_calls` counts the LLM calls of the same request that a later
 * call replaced; `retry_cost_usd` is what they cost. Both are null for a trace stored before the
 * columns existed, and the cost alone is null when a retried call has no known price. Unknown is
 * "—" with one of the two shared reasons (`lib/retry-reasons.ts`), never 0.
 */
/** The tooltip line under a known count in the list. */
export const RETRY_LIST_EXPLANATION = "Earlier attempts of the same request in this trace";
/** The info tooltip of the trace header line. */
export const RETRY_HEADER_EXPLANATION =
  "Earlier attempts of the same request in this trace (same request hash). The last attempt is not counted. Gateway retries inside one call are in the span’s attempt log instead.";

const DASH = "—";

export interface RetryText {
  count: string;
  cost: string;
  /** Why a dash is shown; null when both values are known. */
  reason: string | null;
}

export function formatRetries(calls: number | null, cost: string | null): RetryText {
  if (calls === null) {
    return { count: DASH, cost: DASH, reason: RETRY_NOT_COMPUTED_REASON };
  }
  const count = String(calls);
  if (calls === 0) {
    // No retried call: a known zero, whatever the cost column holds.
    return { count, cost: formatCost(0) ?? "$0.00", reason: null };
  }
  const formatted = formatCost(cost);
  if (formatted === null) {
    return { count, cost: DASH, reason: RETRY_NO_KNOWN_COST_REASON };
  }
  return { count, cost: formatted, reason: null };
}

/** "1 retry", "3 retries". */
export function retriesNoun(calls: number): string {
  return calls === 1 ? "1 retry" : `${String(calls)} retries`;
}

/**
 * The trace header line and the list tooltip's title: "3 retries · $0.0412 retry cost", or
 * "Retries — · retry cost —" when not computed. Null for a trace without retries.
 */
export function retrySummary(calls: number | null, cost: string | null): string | null {
  if (calls === 0) {
    return null;
  }
  const text = formatRetries(calls, cost);
  if (calls === null) {
    return `Retries ${DASH} · retry cost ${DASH}`;
  }
  return `${retriesNoun(calls)} · ${text.cost} retry cost`;
}

/** The phone tile's badge: "3 retries · $0.0291", "3 retries · —", "Retries —"; null for 0. */
export function retryBadgeText(calls: number | null, cost: string | null): string | null {
  if (calls === 0) {
    return null;
  }
  if (calls === null) {
    return `Retries ${DASH}`;
  }
  return `${retriesNoun(calls)} · ${formatRetries(calls, cost).cost}`;
}

/** The list's Retry cost column: absent, shown, or shown as the sort key. */
export type RetryCostColumn = "hidden" | "shown" | "sorted";

/**
 * The "Retry cost" column shows only while it is the sort key or the has-retries filter is on, so
 * the default list keeps its width for the columns most people read.
 */
export function retryCostColumn(search: {
  sort?: TraceSort | undefined;
  has_retries?: boolean | undefined;
}): RetryCostColumn {
  if (search.sort === "retry_cost_usd") {
    return "sorted";
  }
  return search.has_retries === true ? "shown" : "hidden";
}

/** The phone list's footnote, needed only when a tile shows a "—" form. */
export const RETRY_TILES_FOOTNOTE =
  "Retry cost sits next to the count; the whole tile is one link. “—” after a count: a retried call has no known cost. “Retries —”: not computed for traces stored before 2026-10-15.";

/** Whether any of these traces shows a "—" retries form (a count or cost that is unknown). */
export function hasUnknownRetries(
  traces: readonly { retry_calls: number | null; retry_cost_usd: string | null }[],
): boolean {
  return traces.some(
    (trace) => formatRetries(trace.retry_calls, trace.retry_cost_usd).reason !== null,
  );
}
