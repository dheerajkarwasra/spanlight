import type { Bucket, ReleaseStats } from "@/lib/api";
import type { ResolvedRange } from "@/lib/time-range";

import { BUCKET_MS } from "./chart-data";

/** The releases API answers 422 for a window over 30 days (spec ruling 12). */
export const RELEASE_WINDOW_MAX_MS = 30 * 24 * 3600_000;

export const RELEASE_WINDOW_NOTE = "Release markers cover 30 days or less.";

const MARKER_LIMIT = 8;

export interface ReleaseMarker {
  release: string;
  at: string;
}

/**
 * Releases first seen inside the window, the 8 most recent, newest first. `first_seen_at` is the
 * first trace inside the window, so a release already live before it reads as starting in the
 * window's first bucket: markers there are dropped.
 */
export function markersInWindow(
  releases: readonly ReleaseStats[],
  from: string,
  to: string,
  bucket: Bucket,
): ReleaseMarker[] {
  const step = BUCKET_MS[bucket];
  const firstBucketEnd = Math.floor(Date.parse(from) / step) * step + step;
  const end = Date.parse(to);
  return releases
    .map((release) => ({ release: release.release, at: release.first_seen_at }))
    .filter((marker) => {
      const at = Date.parse(marker.at);
      return at >= firstBucketEnd && at <= end;
    })
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .slice(0, MARKER_LIMIT);
}

/**
 * The markers the chart draws. A window over 30 days has none: the query is disabled there, but
 * TanStack still hands back the previous window's releases as placeholder data.
 */
export function windowMarkers(
  releases: readonly ReleaseStats[] | undefined,
  range: Pick<ResolvedRange, "from" | "to" | "durationMs">,
  bucket: Bucket,
): ReleaseMarker[] {
  if (!releases || range.durationMs > RELEASE_WINDOW_MAX_MS) {
    return [];
  }
  return markersInWindow(releases, range.from, range.to, bucket);
}

export interface MarkerLine {
  /** The x category (bucket start) the dashed line is drawn on. */
  bucketStart: string;
  /** The release names first seen in that bucket, newest first. */
  label: string;
}

/** One line per bucket that holds a marker; markers outside the drawn buckets are dropped. */
export function markerLines(
  markers: readonly ReleaseMarker[],
  points: readonly { bucketStart: string }[],
  bucket: Bucket,
): MarkerLine[] {
  const step = BUCKET_MS[bucket];
  const lines = new Map<string, string[]>();
  for (const marker of markers) {
    const at = Date.parse(marker.at);
    const point = points.find((candidate) => {
      const start = Date.parse(candidate.bucketStart);
      return at >= start && at < start + step;
    });
    if (point) {
      lines.set(point.bucketStart, [...(lines.get(point.bucketStart) ?? []), marker.release]);
    }
  }
  return [...lines].map(([bucketStart, names]) => ({ bucketStart, label: names.join(", ") }));
}
