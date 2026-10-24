/**
 * Display helpers for session times and counts. Pure functions only; every
 * clock time is shown in the viewer's local time zone.
 */
import { formatClock, formatDayTime, isSameDay, parseDate } from "@/lib/clock-format";
import type { SessionTurn } from "@/lib/api";
import { formatCost, formatDuration, parseMoney, pluralize } from "@/lib/format";

export { dayLabel, formatClock, formatDayTime } from "@/lib/clock-format";

/**
 * First → last activity, e.g. "Today 09:12 → 09:41". The day is repeated only
 * when the session crosses midnight: "Yesterday 23:50 → Today 00:10".
 */
export function formatSessionWindow(
  firstIso: string,
  lastIso: string,
  now: Date = new Date(),
): string | null {
  const first = parseDate(firstIso);
  const last = parseDate(lastIso);
  const start = formatDayTime(firstIso, now);
  if (first === null || last === null || start === null) {
    return null;
  }
  const end = isSameDay(first, last) ? formatClock(lastIso) : formatDayTime(lastIso, now);
  return `${start} → ${end ?? ""}`;
}

/** A short duration for list rows: "45s", "29m", "2h 5m". Null when unknown. */
export function formatCompactDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return null;
  }
  if (ms < 1000) {
    return "<1s";
  }
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

/** "Turns 1–10 of 24", "12 turns", or "Latest 100 turns" while older turns aren't loaded. */
export function turnRangeLabel(visible: number, loaded: number, hasEarlierTurns: boolean): string {
  const loadedLabel = loaded.toLocaleString("en-US");
  if (hasEarlierTurns) {
    return `Latest ${loadedLabel} turns`;
  }
  if (visible < loaded) {
    return `Turns 1–${visible.toLocaleString("en-US")} of ${loadedLabel}`;
  }
  return pluralize(loaded, "turn");
}

/** A session's cost above the window's 99th percentile; never when either figure is unknown. */
export function isCostOutlier(cost: string | null, p99: string | null): boolean {
  const value = parseMoney(cost);
  const limit = parseMoney(p99);
  return value !== null && limit !== null && value > limit;
}

/**
 * "4 steps · 2 tool calls · 1.8 s · $0.014"; an unknown duration or cost shows "—", and `reason`
 * says why (null when every figure is known).
 */
export function turnPillText({ trace, tool_calls }: SessionTurn): {
  text: string;
  reason: string | null;
} {
  const duration = formatDuration(trace.duration_ms);
  const cost = formatCost(trace.cost_usd);
  const reasons = [
    duration === null ? "No timing data" : null,
    cost === null ? "No price for the models used" : null,
  ].filter((reason) => reason !== null);
  return {
    text: [
      pluralize(trace.span_count, "step"),
      pluralize(tool_calls, "tool call"),
      duration ?? "—",
      cost ?? "—",
    ].join(" · "),
    reason: reasons.length > 0 ? reasons.join("; ") : null,
  };
}
