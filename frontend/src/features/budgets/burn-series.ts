import type { BudgetBurn } from "@/lib/api";
import { parseMoney } from "@/lib/format";

import { percentLabel } from "./budget-period";
import { shortUsd } from "./budget-ticks";

/** One day of the burn line: spend to date, or null before the first priced day (never 0). */
export interface BurnPoint {
  day: string;
  cumulative: number | null;
  amount: number;
}

export function burnPoints(burn: BudgetBurn): BurnPoint[] {
  const amount = Number(burn.amount_usd);
  return burn.points.map((point) => ({
    day: point.day,
    cumulative: parseMoney(point.cumulative_usd),
    amount,
  }));
}

const DAY = 86_400_000;

/** Every UTC day from `period_start` up to `resets_at` (exclusive): the chart's x axis. */
export function periodDays(burn: Pick<BudgetBurn, "period_start" | "resets_at">): string[] {
  const start = Math.floor(Date.parse(burn.period_start) / DAY) * DAY;
  const end = Date.parse(burn.resets_at);
  const days: string[] = [];
  // Capped at 31: a period is at most a month.
  for (let at = start; at < end && days.length < 31; at += DAY) {
    days.push(new Date(at).toISOString().slice(0, 10));
  }
  return days;
}

const dayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** "Oct 14": a "YYYY-MM-DD" UTC day. */
export function dayLabel(day: string): string {
  return dayFormat.format(new Date(`${day}T00:00:00Z`));
}

/** "Oct 14 · $182 of $500 (36 %)"; an unknown day reads "Oct 1 · — of $500". */
export function burnTooltip(point: BurnPoint): string {
  const of = `of ${shortUsd(point.amount)}`;
  return point.cumulative === null
    ? `${dayLabel(point.day)} · — ${of}`
    : `${dayLabel(point.day)} · ${shortUsd(point.cumulative)} ${of} (${percentLabel(point.cumulative / point.amount)})`;
}

/** The period's last UTC day and the forecast spend on it; null without a forecast. */
export interface BurnForecast {
  lastDay: string;
  usd: number | null;
  basisDays: number;
  /** The day spend passes the amount, and whether that day has already come. */
  crossing: { day: string; passed: boolean } | null;
}

/** Null for a one-day period (a daily budget has no forecast); otherwise what the API forecast. */
export function burnForecast(burn: BudgetBurn): BurnForecast | null {
  const days = periodDays(burn);
  const lastDay = days.at(-1);
  if (lastDay === undefined || days.length === 1) return null;
  const crossesOn = burn.forecast_crosses_on;
  // Measured when that day's spend to date is already over the amount; a forecast crossing can
  // fall on today while today's spend is still under it.
  const spentThen = parseMoney(
    burn.points.find((point) => point.day === crossesOn)?.cumulative_usd,
  );
  const passed = spentThen !== null && spentThen > Number(burn.amount_usd);
  return {
    lastDay,
    usd: parseMoney(burn.forecast_usd),
    basisDays: burn.forecast_basis_days,
    crossing: crossesOn === null ? null : { day: crossesOn, passed },
  };
}
