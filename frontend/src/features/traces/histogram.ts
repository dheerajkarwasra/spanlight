import type { Bucket, TraceHistogram } from "@/lib/api";
import { pluralize } from "@/lib/format";

const BUCKET_MS: Record<Bucket, number> = { hour: 3_600_000, day: 86_400_000 };

/** One bar of the traces histogram: the bucket's ISO start, its traces and the failed ones. */
export interface HistogramBar {
  start: string;
  traces: number;
  errors: number;
}

/**
 * Every bucket of the window, oldest first, from the one holding `from` (the server bins whole
 * UTC hours or days) up to `to`. The server leaves out buckets without a trace; they are zeros.
 */
export function fillBuckets(
  response: TraceHistogram,
  window: { from: string; to: string },
): HistogramBar[] {
  const step = BUCKET_MS[response.bucket];
  const counts = new Map(response.buckets.map((b) => [Date.parse(b.bucket_start), b]));
  const to = Date.parse(window.to);
  const bars: HistogramBar[] = [];
  for (let start = Math.floor(Date.parse(window.from) / step) * step; start < to; start += step) {
    const found = counts.get(start);
    bars.push({
      start: new Date(start).toISOString(),
      traces: found?.traces ?? 0,
      errors: found?.errors ?? 0,
    });
  }
  return bars;
}

const hourFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
// Day buckets start at UTC midnight; formatting them in UTC keeps "Oct 14" from reading "Oct 13"
// west of Greenwich.
const dayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** "Oct 14 · 132 traces · 3 errors"; an hour reads "Oct 14, 14:00 · …" in local time. */
export function bucketLabel(bar: HistogramBar, bucket: Bucket): string {
  const format = bucket === "hour" ? hourFormat : dayFormat;
  return `${format.format(new Date(bar.start))} · ${pluralize(bar.traces, "trace")} · ${pluralize(bar.errors, "error")}`;
}

/**
 * The custom range that narrows the list to one bucket, clipped to the window the histogram
 * counted, so a partial first or last bar opens exactly the traces it counted.
 */
export function bucketWindow(
  start: string,
  bucket: Bucket,
  window: { from: string; to: string },
): { from: string; to: string } {
  const from = Math.max(Date.parse(start), Date.parse(window.from));
  const to = Math.min(Date.parse(start) + BUCKET_MS[bucket], Date.parse(window.to));
  return { from: new Date(from).toISOString(), to: new Date(to).toISOString() };
}

/**
 * The least height (px) a bar's mark keeps: 2 px per segment it shows, plus the 2 px gap between
 * them, so a tiny bar with failed and succeeded traces still shows both.
 */
export function markMinHeight(bar: HistogramBar): number {
  return bar.errors > 0 && bar.errors < bar.traces ? 6 : 2;
}
