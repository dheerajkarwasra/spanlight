import type { ModelMetrics } from "@/lib/api";
import { formatCost, parseMoney } from "@/lib/format";

import type { ChartPoint } from "./chart-data";

/**
 * Pure helpers for the Spend card: the split headline amount, the monthly projection, the
 * peak bar and the top models by share of cost.
 */

const DAY_MS = 24 * 3600_000;

/** Projections only make sense for short windows; a 30-day window already is a month. */
export const PROJECTION_MAX_WINDOW_MS = 7 * DAY_MS;

/** Same precision rules as `formatCost`: more decimals for small amounts so they never read $0.00. */
function fractionDigitsFor(amount: number): number {
  const absolute = Math.abs(amount);
  if (absolute === 0 || absolute >= 1) {
    return 2;
  }
  return absolute < 0.01 ? 6 : 4;
}

export interface SplitAmount {
  /** "$1,184" — the part shown at full strength. */
  whole: string;
  /** ".27" — the muted cents (and sub-cent digits for small amounts). */
  fraction: string;
}

/** "$1,184.27" split into "$1,184" and ".27" for the headline number. */
export function splitCurrency(amount: number): SplitAmount {
  const digits = fractionDigitsFor(amount);
  const formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
  const point = formatted.lastIndexOf(".");
  if (point === -1) {
    return { whole: formatted, fraction: "" };
  }
  return { whole: formatted.slice(0, point), fraction: formatted.slice(point) };
}

/** Midnight UTC on the first of the month `months` after the UTC month of `date`. */
export function utcMonthStart(date: Date, months = 0): number {
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1);
}

/**
 * Month-end spend at the window's rate, on the UTC month budgets reset on: the month's spend so
 * far plus the window's spend per day of data × the time left. Null when either spend is
 * unknown, the window is longer than a week (it already says more than an extrapolation would)
 * or it holds under 24 h of data.
 */
export function projectMonthlySpend(
  costUsd: number | null,
  monthToDateUsd: number | null,
  points: readonly ChartPoint[] | null,
  windowMs: number,
  now: Date,
): number | null {
  if (costUsd === null || monthToDateUsd === null || points === null) {
    return null;
  }
  const first = points.find((point) => point.llmCalls > 0);
  if (first === undefined || windowMs > PROJECTION_MAX_WINDOW_MS) {
    return null;
  }
  const windowStart = now.getTime() - windowMs;
  const heldMs = now.getTime() - Math.max(windowStart, Date.parse(first.bucketStart));
  if (!(heldMs >= DAY_MS)) {
    return null;
  }
  return monthToDateUsd + (costUsd / heldMs) * (utcMonthStart(now, 1) - now.getTime());
}

const compactCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const wholeCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/** Short money for secondary lines: "$5.4K" from $1,000, otherwise two decimals ("$0.42"). */
export function formatCompactCost(amount: number): string {
  if (Math.abs(amount) >= 1000) {
    return compactCurrency.format(amount);
  }
  if (amount !== 0 && Math.abs(amount) < 0.01) {
    return "<$0.01";
  }
  return `$${amount.toFixed(2)}`;
}

/** Tile money: whole dollars from $10 ("$112"), cents below ("$4.12"), "<$0.01" for slivers. */
export function formatTileCost(amount: number): string {
  if (Math.abs(amount) >= 10) {
    return wholeCurrency.format(amount);
  }
  if (amount !== 0 && Math.abs(amount) < 0.01) {
    return "<$0.01";
  }
  return `$${amount.toFixed(2)}`;
}

/**
 * Index of the highest known value over the complete buckets (`partial[index]` false), or null
 * when nothing is above zero. Ties go to the most recent bucket, so a flat series highlights "now".
 */
export function peakIndex(
  values: readonly (number | null)[],
  partial: readonly boolean[] = [],
): number | null {
  let peak: number | null = null;
  let peakValue = 0;
  for (const [index, value] of values.entries()) {
    if (partial[index] !== true && value !== null && value > 0 && value >= peakValue) {
      peak = index;
      peakValue = value;
    }
  }
  return peak;
}

export interface ModelShare {
  /** Stable key: provider/model. */
  key: string;
  model: string;
  costUsd: number;
  /** Fraction of the priced spend, 0–1. */
  share: number;
}

/** The `limit` most expensive priced models and their share of the total priced spend. */
export function topModelsByCost(models: readonly ModelMetrics[], limit = 3): ModelShare[] {
  const priced = models
    .map((model) => ({ model, cost: parseMoney(model.cost_usd) }))
    .filter((entry): entry is { model: ModelMetrics; cost: number } => {
      return entry.cost !== null && entry.cost > 0;
    });
  const total = priced.reduce((sum, entry) => sum + entry.cost, 0);
  if (total <= 0) {
    return [];
  }
  return priced
    .sort((a, b) => b.cost - a.cost)
    .slice(0, limit)
    .map(({ model, cost }) => ({
      key: `${model.provider ?? "∅"}/${model.model ?? "∅"}`,
      model: model.model ?? "Unknown model",
      costUsd: cost,
      share: cost / total,
    }));
}

/** "61%", or "<1%" for a sliver that would otherwise round to zero. */
export function formatShare(share: number): string {
  const percent = Math.round(share * 100);
  if (percent === 0 && share > 0) {
    return "<1%";
  }
  return `${percent}%`;
}

/**
 * A compact label for a model tile, e.g. "claude-sonnet-4-5" → "Sonnet 4.5",
 * "gpt-4.1-mini" → "4.1-mini". Drops the vendor prefix and a trailing release date; anything
 * it doesn't recognise is returned unchanged (the full name stays in the tile's title).
 */
export function shortModelLabel(model: string): string {
  const withoutVendor = model.replace(/^(claude|gpt)-/i, "");
  const withoutDate = withoutVendor.replace(/-(\d{8}|\d{4}-\d{2}-\d{2})$/, "");
  const familyVersion = /^([a-z]+)-(\d+)-(\d+)$/i.exec(withoutDate);
  if (familyVersion) {
    const [, family = "", major = "", minor = ""] = familyVersion;
    return `${family.charAt(0).toUpperCase()}${family.slice(1)} ${major}.${minor}`;
  }
  return withoutDate || model;
}

/**
 * Indexes of the buckets that get an axis label: the latest bucket and then every `step`
 * buckets back, at most `maxTicks` labels. Returned oldest first.
 */
export function relativeTickIndexes(count: number, maxTicks = 5): number[] {
  if (count <= 0) {
    return [];
  }
  const last = count - 1;
  const step = Math.max(1, Math.ceil(last / (maxTicks - 1)));
  const indexes: number[] = [];
  for (let index = last; index >= 0; index -= step) {
    indexes.push(index);
  }
  return indexes.reverse();
}

/** "Now", "−6h" or "−2d": how far a bucket lies before the latest one. */
export function relativeBucketLabel(stepsBack: number, bucket: "hour" | "day"): string {
  if (stepsBack <= 0) {
    return bucket === "hour" ? "Now" : "Today";
  }
  return `−${stepsBack}${bucket === "hour" ? "h" : "d"}`;
}

/**
 * Spend axis tick: "$0", "$0.0005", "$0.25", "$40", "$1.2K". `formatCost`'s sub-cent precision
 * without its trailing zeros, so the axis stays narrow.
 */
export function formatAxisCost(amount: number): string {
  if (amount === 0) {
    return "$0";
  }
  if (Math.abs(amount) >= 10) {
    return Math.abs(amount) >= 1000 ? formatCompactCost(amount) : formatTileCost(amount);
  }
  return (formatCost(amount) ?? "").replace(/(\.\d{2}\d*?)0+$/, "$1");
}
