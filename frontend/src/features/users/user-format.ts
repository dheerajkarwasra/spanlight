/**
 * Display helpers for the Users pages. Pure functions only. The API groups activity by UTC day,
 * so days are formatted in UTC; formatting them locally would shift "Oct 7" for some viewers.
 */
import type { UserDayStats, UserStatsWindow } from "@/lib/api";
import { formatCost, parseMoney } from "@/lib/format";

const monthDayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const weekdayFormat = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function parseDay(day: string): Date | null {
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "Oct 6" for a `YYYY-MM-DD` UTC day; the raw value when it can't be parsed. */
export function formatUtcDay(day: string): string {
  const date = parseDay(day);
  return date === null ? day : monthDayFormat.format(date);
}

/** "Today", "Yesterday" or "Oct 6": when a user was last active, by UTC day. */
export function lastSeenLabel(day: string, now: Date = new Date()): string {
  const today = now.toISOString().slice(0, 10);
  const yesterday = new Date(now.getTime() - 24 * 3600_000).toISOString().slice(0, 10);
  if (day === today) {
    return "Today";
  }
  return day === yesterday ? "Yesterday" : formatUtcDay(day);
}

/** "last seen" wording: "today", "yesterday" or "Oct 6" (a date keeps its capital). */
export function lastSeenPhrase(day: string, now: Date = new Date()): string {
  const label = lastSeenLabel(day, now);
  return label === "Today" || label === "Yesterday" ? label.toLowerCase() : label;
}

/** "Oct 4 – Oct 10": the response window; its end is exclusive, so the last day is one before. */
export function windowLabel(window: UserStatsWindow): string {
  const start = new Date(window.start);
  const end = new Date(window.end);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return "";
  }
  const last = new Date(end.getTime() - 1);
  return `${monthDayFormat.format(start)} – ${monthDayFormat.format(last)}`;
}

/** One UTC day of the daily chart. `cost` is null when the day had calls but no known price. */
export interface DailyPoint {
  day: string;
  label: string;
  tooltipLabel: string;
  calls: number;
  errors: number;
  cost: number | null;
  /** Some calls had no price: a known cost is only a lower bound. */
  lowerBound: boolean;
  costText: string | null;
}

export function toDailyPoints(daily: readonly UserDayStats[]): DailyPoint[] {
  return daily.map((day) => {
    const date = parseDay(day.day);
    return {
      day: day.day,
      label: formatUtcDay(day.day),
      tooltipLabel: date === null ? day.day : weekdayFormat.format(date),
      calls: day.llm_calls,
      errors: day.errors,
      cost: parseMoney(day.cost_usd),
      costText: formatCost(day.cost_usd),
      lowerBound: day.unpriced_calls > 0 && day.cost_usd !== null,
    };
  });
}

/** Whether any day has LLM calls; an all-zero window shows an empty plot message instead. */
export function hasDailyCalls(points: readonly DailyPoint[]): boolean {
  return points.some((point) => point.calls > 0);
}

export const NO_USER_PRICE = "No price for the models this user called.";
