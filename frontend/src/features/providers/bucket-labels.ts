import type { Bucket } from "@/lib/api";

/*
 * The page reads in UTC (its window, incidents and buckets say so), so bucket labels are UTC too:
 * the server buckets by UTC hour and UTC day.
 */

const hourFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

const dayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function parse(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Axis tick: "06:00" for hours, "Oct 16" for days. */
export function bucketTick(iso: string, bucket: Bucket): string {
  const date = parse(iso);
  if (date === null) {
    return "";
  }
  return bucket === "hour" ? hourFormat.format(date) : dayFormat.format(date);
}

/** Tooltip and table label: "Oct 16 · 01:00 – 02:00" for hours, "Oct 16" for days. */
export function bucketRange(iso: string, bucket: Bucket): string {
  const date = parse(iso);
  if (date === null) {
    return "";
  }
  if (bucket === "day") {
    return dayFormat.format(date);
  }
  const end = new Date(date.getTime() + 3600_000);
  return `${dayFormat.format(date)} · ${hourFormat.format(date)} – ${hourFormat.format(end)}`;
}

const clockFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** "05:27:41" in UTC; null for a time that does not parse. */
export function utcClock(iso: string): string | null {
  const date = parse(iso);
  return date === null ? null : clockFormat.format(date);
}

/** "Oct 16, 05:30": a moment in UTC, for the window and incident lines. */
export function utcMoment(iso: string): string | null {
  const date = parse(iso);
  return date === null ? null : `${dayFormat.format(date)}, ${hourFormat.format(date)}`;
}
