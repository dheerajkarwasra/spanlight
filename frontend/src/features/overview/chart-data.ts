import type { Bucket, TimeseriesPoint } from "@/lib/api";
import { parseMoney } from "@/lib/format";
import { bucketFor, type ResolvedRange } from "@/lib/time-range";

export const BUCKET_MS: Record<Bucket, number> = { hour: 3_600_000, day: 86_400_000 };

/** One timeseries bucket, reshaped for Recharts. Unknown values stay null (drawn as gaps). */
export interface ChartPoint {
  /** ISO bucket start; also the category key on the x axis. */
  bucketStart: string;
  llmCalls: number;
  errors: number;
  /** `llmCalls − errors`: the lower segment of the calls stack. */
  succeeded: number;
  /**
   * The bucket is not whole: the window clips it (the first) or it is still filling (the last).
   * Its sums are real; charts draw it fainter, label it "partial" and leave it out of the peak.
   */
  partial: boolean;
  p95Ms: number | null;
  costUsd: number | null;
  tokens: number;
}

/**
 * The backend returns gapless UTC buckets from the one holding the window's start
 * (`app/metrics/service.py: timeseries`), so the first and last buckets can be partial.
 */
export function toChartPoints(
  points: readonly TimeseriesPoint[],
  range: Pick<ResolvedRange, "from" | "to" | "durationMs">,
): ChartPoint[] {
  const step = BUCKET_MS[bucketFor(range.durationMs)];
  const from = Date.parse(range.from);
  const to = Date.parse(range.to);
  return points.map((point) => {
    const start = Date.parse(point.bucket_start);
    return {
      bucketStart: point.bucket_start,
      llmCalls: point.llm_calls,
      errors: point.errors,
      succeeded: point.llm_calls - point.errors,
      partial: start < from || start + step > to,
      p95Ms: point.p95_ms,
      costUsd: parseMoney(point.cost_usd),
      tokens: point.tokens,
    };
  });
}

const hourTickFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

// Daily buckets start at UTC midnight; formatting them in UTC keeps "Oct 7"
// from shifting to "Oct 6" for viewers west of Greenwich.
const dayTickFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const dayWithWeekdayFormat = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const localDayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

const hourTooltipFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function parse(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Axis tick: "14:00" for hourly buckets, "Oct 7" for daily ones. */
export function formatBucketTick(iso: string, bucket: Bucket): string {
  const date = parse(iso);
  if (!date) {
    return "";
  }
  return bucket === "hour" ? hourTickFormat.format(date) : dayTickFormat.format(date);
}

/** Table and screen-reader label: "Oct 7, 14:00" for hourly buckets, "Oct 7" for daily ones. */
export function formatBucketLabel(iso: string, bucket: Bucket): string {
  const date = parse(iso);
  if (!date) {
    return "";
  }
  return bucket === "hour" ? hourTooltipFormat.format(date) : dayTickFormat.format(date);
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/**
 * Tooltip heading. Hourly: "Today · 10:00 – 11:00", "Yesterday · 23:00 – 00:00" or
 * "Oct 6 · 09:00 – 10:00". Daily: "Today", "Yesterday" or "Mon, Oct 6". A partial bucket gets
 * " · partial".
 */
export function formatBucketRange(
  iso: string,
  bucket: Bucket,
  now: Date = new Date(),
  partial = false,
): string {
  const label = bucketRangeLabel(iso, bucket, now);
  return label && partial ? `${label} · partial` : label;
}

function bucketRangeLabel(iso: string, bucket: Bucket, now: Date): string {
  const start = parse(iso);
  if (!start) {
    return "";
  }

  if (bucket === "day") {
    const todayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    const daysBack = Math.round((todayUtc - start.getTime()) / (24 * 3600_000));
    if (daysBack === 0) {
      return "Today";
    }
    if (daysBack === 1) {
      return "Yesterday";
    }
    return dayWithWeekdayFormat.format(start);
  }

  const end = new Date(start.getTime() + 3600_000);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let day = localDayFormat.format(start);
  if (isSameLocalDay(start, now)) {
    day = "Today";
  } else if (isSameLocalDay(start, yesterday)) {
    day = "Yesterday";
  }
  return `${day} · ${hourTickFormat.format(start)} – ${hourTickFormat.format(end)}`;
}

/** "hour" / "day", for sentences like "per hour". */
export function bucketNoun(bucket: Bucket): string {
  return bucket === "hour" ? "hour" : "day";
}

/** "Hourly" / "Daily", for card descriptions. */
export function bucketAdjective(bucket: Bucket): string {
  return bucket === "hour" ? "Hourly" : "Daily";
}

export interface CallsSummary {
  calls: number;
  errors: number;
}

export function summarizeCalls(points: readonly ChartPoint[]): CallsSummary {
  let calls = 0;
  let errors = 0;
  for (const point of points) {
    calls += point.llmCalls;
    errors += point.errors;
  }
  return { calls, errors };
}

export function hasCallData(points: readonly ChartPoint[]): boolean {
  return points.some((point) => point.llmCalls > 0);
}

export function hasCostData(points: readonly ChartPoint[]): boolean {
  return points.some((point) => point.costUsd !== null && point.costUsd > 0);
}
