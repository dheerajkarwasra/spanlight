import type { GatewaySeries } from "@/lib/api";
import { formatBucketTime, pluralize } from "@/lib/format";

/** One bar of "Requests over time": the bucket's succeeded calls with its errors on top. */
export interface RequestsBar {
  start: string;
  /** "14:00", or "Oct 14, 09:00" when the bars span more than one day. */
  label: string;
  ok: number;
  errors: number;
}

export function requestsBars(series: GatewaySeries): RequestsBar[] {
  const hours = series.bucket === "hour" ? series.points.length : 0;
  return series.points.map((point) => ({
    start: point.bucket_start,
    label: formatBucketTime(point.bucket_start, hours),
    ok: Math.max(point.requests - point.errors, 0),
    errors: point.errors,
  }));
}

/** "14:00 · 132 requests · 3 errors": the bar's tooltip. */
export function barSummary(bar: RequestsBar): string {
  return `${bar.label} · ${pluralize(bar.ok + bar.errors, "request")} · ${pluralize(bar.errors, "error")}`;
}
