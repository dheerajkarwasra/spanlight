import type { AttributionSeries } from "@/lib/api";
import { formatBucketTime, formatCompact, formatCost, parseMoney, pluralize } from "@/lib/format";

/** One bar of "Spend over time". */
export interface SpendBar {
  start: string;
  /** "Oct 14" (UTC day), "14:00", or "Oct 14, 09:00" when hourly bars span more than a day. */
  label: string;
  /** `null` when the bucket's calls had no price: no bar, never a zero. */
  cost: number | null;
  /** Some of the bucket's calls had no price, so the bar is a lower bound. */
  lowerBound: boolean;
  unpriced: number;
}

const dayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

export const PENDING_NOTE = "Some of today's traces aren't counted yet";

export function seriesBars(series: AttributionSeries): SpendBar[] {
  const hourly = series.bucket === "hour";
  return series.points.map((point) => {
    const cost = parseMoney(point.cost_usd);
    return {
      start: point.bucket_start,
      // Rollup days are UTC days; hours read in the viewer's time.
      label: hourly
        ? formatBucketTime(point.bucket_start, series.points.length)
        : dayFormat.format(new Date(point.bucket_start)),
      cost,
      lowerBound: cost !== null && point.unpriced_calls > 0,
      unpriced: point.unpriced_calls,
    };
  });
}

/** "Oct 14 · $4.12 · 3 unpriced calls" (spec §3.3). */
export function barSummary(bar: SpendBar): string {
  const cost = formatCost(bar.cost) ?? "No priced calls";
  return `${bar.label} · ${cost} · ${pluralize(bar.unpriced, "unpriced call")}`;
}

/**
 * The tooltip: the summary, "Lower bound" when some calls had no price, and on the last bar the
 * note that the rollup hasn't counted all of today's traces yet (a day not rolled up reads 0).
 */
export function barLines(bar: SpendBar, last: boolean, pendingTraces: number | null): string[] {
  const lines = [barSummary(bar)];
  if (bar.lowerBound) {
    lines.push("Lower bound");
  }
  if (last && pendingTraces !== null && pendingTraces > 0) {
    lines.push(PENDING_NOTE);
  }
  return lines;
}

/**
 * A y tick: whole dollars from $1k ("$1,500", "$12K"), cents from a dime ("$0.80", never
 * "$0.8000"); smaller ticks keep their digits.
 */
export function spendTick(value: number): string {
  if (value >= 1000) return `$${formatCompact(value) ?? ""}`;
  // Under $1k, so no thousands separator to add.
  if (value >= 0.1) return `$${value.toFixed(2)}`;
  return formatCost(value) ?? "";
}
