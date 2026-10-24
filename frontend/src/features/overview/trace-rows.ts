import type { TraceSummary } from "@/lib/api";
import { formatInteger, shortId } from "@/lib/format";

/** Pure helpers for the Latest traces tiles and the Recent errors list. */

const MINUTE_S = 60;
const HOUR_S = 60 * MINUTE_S;
const DAY_S = 24 * HOUR_S;

/** Compact age for dense rows: "12s", "4m", "3h", "2d". Null for an unparseable timestamp. */
export function formatAge(iso: string, now: Date = new Date()): string | null {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) {
    return null;
  }
  const seconds = Math.max(0, Math.floor((now.getTime() - time) / 1000));
  if (seconds < MINUTE_S) {
    return `${seconds}s`;
  }
  if (seconds < HOUR_S) {
    return `${Math.floor(seconds / MINUTE_S)}m`;
  }
  if (seconds < DAY_S) {
    return `${Math.floor(seconds / HOUR_S)}h`;
  }
  return `${Math.floor(seconds / DAY_S)}d`;
}

/** The spoken form of `formatAge`: "12 seconds ago", "4 minutes ago". */
export function describeAge(iso: string, now: Date = new Date()): string | null {
  const age = formatAge(iso, now);
  if (age === null) {
    return null;
  }
  const amount = Number(age.slice(0, -1));
  const unit = { s: "second", m: "minute", h: "hour", d: "day" }[age.slice(-1)] ?? "second";
  return `${amount} ${unit}${amount === 1 ? "" : "s"} ago`;
}

/** The trace's name, or "Trace 1a2b3c4d" when it was sent without one. */
export function traceDisplayName(trace: Pick<TraceSummary, "name" | "trace_id">): string {
  return trace.name ?? `Trace ${shortId(trace.trace_id)}`;
}

/** "claude-sonnet-4-5", "claude-sonnet-4-5 +1" for several models, or null when none was called. */
export function primaryModelLabel(models: readonly string[]): string | null {
  const [first] = models;
  if (first === undefined) {
    return null;
  }
  return models.length > 1 ? `${first} +${models.length - 1}` : first;
}

/**
 * "1,840 tok", or null when no call reported token usage or the trace made no LLM calls (then
 * there is no token count to show, as opposed to a real zero).
 */
export function tokenLabel(
  trace: Pick<TraceSummary, "input_tokens" | "output_tokens" | "models">,
): string | null {
  if (trace.input_tokens === null && trace.output_tokens === null) {
    return null;
  }
  const total = (trace.input_tokens ?? 0) + (trace.output_tokens ?? 0);
  if (total === 0 && trace.models.length === 0) {
    return null;
  }
  return `${formatInteger(total) ?? "0"} tok`;
}

/** "1 failed span" / "2 failed spans", or null when nothing failed. */
export function failedSpansLabel(errorCount: number): string | null {
  if (errorCount <= 0) {
    return null;
  }
  return `${formatInteger(errorCount) ?? String(errorCount)} failed ${errorCount === 1 ? "span" : "spans"}`;
}
