import type {
  ErrorBucket,
  ErrorClass,
  RateLimitKind,
  RateLimitKindCount,
  StatusSource,
} from "@/lib/api";
import { ERROR_CLASSES } from "@/lib/error-class";
import { RATE_LIMIT_KIND_ORDER } from "@/lib/rate-limit-kind";

/** One bucket of the errors-by-class chart. */
export interface StackedPoint {
  bucketStart: string;
  /** Failed calls per class: every class seen in any bucket, 0 where this bucket has none. */
  counts: Partial<Record<ErrorClass, number>>;
  total: number;
  approximate: boolean;
}

/**
 * Buckets for the stacked bars. Classes absent from every bucket are dropped (no empty legend
 * entries); a class seen anywhere is 0 in the buckets without it, so the stacks line up.
 */
export function toStackedPoints(buckets: readonly ErrorBucket[]): StackedPoint[] {
  const seen = new Set<ErrorClass>();
  for (const bucket of buckets) {
    for (const [name, count] of Object.entries(bucket.by_class)) {
      if (count > 0) {
        seen.add(name as ErrorClass);
      }
    }
  }
  const classes = ERROR_CLASSES.filter((name) => seen.has(name));
  return buckets.map((bucket) => {
    const counts: Partial<Record<ErrorClass, number>> = {};
    let total = 0;
    for (const name of classes) {
      const count = bucket.by_class[name] ?? 0;
      counts[name] = count;
      total += count;
    }
    return { bucketStart: bucket.bucket_start, counts, total, approximate: bucket.approximate };
  });
}

/** The classes the points carry, in the fixed order. */
export function presentClasses(points: readonly StackedPoint[]): ErrorClass[] {
  const first = points[0];
  return first ? ERROR_CLASSES.filter((name) => name in first.counts) : [];
}

/** Overloaded 5xx and the gateway's own key limit are not the provider refusing with a 429. */
const NOT_A_PROVIDER_429: ReadonlySet<RateLimitKind> = new Set(["overloaded", "gateway"]);

export function isProvider429(kind: RateLimitKind): boolean {
  return !NOT_A_PROVIDER_429.has(kind);
}

/** The largest provider 429 kind; a tie goes to the first in kind order; null without one. */
export function dominantKind(kinds: readonly RateLimitKindCount[]): RateLimitKind | null {
  let best: RateLimitKindCount | null = null;
  const rank = (kind: RateLimitKind) => RATE_LIMIT_KIND_ORDER.indexOf(kind);
  for (const candidate of kinds) {
    if (!isProvider429(candidate.kind) || candidate.count <= 0) {
      continue;
    }
    const better =
      best === null ||
      candidate.count > best.count ||
      (candidate.count === best.count && rank(candidate.kind) < rank(best.kind));
    if (better) {
      best = candidate;
    }
  }
  return best?.kind ?? null;
}

/** A feed that has not answered for this long is shown as stale. */
export const STALE_FEED_MS = 30 * 60_000;

/** More than 30 minutes since the last good poll, a failed latest poll, or never fetched. */
export function staleFeed(source: StatusSource, now: Date): boolean {
  if (source.consecutive_failures > 0 || source.last_success_at === null) {
    return true;
  }
  const lastSuccess = Date.parse(source.last_success_at);
  return Number.isNaN(lastSuccess) || now.getTime() - lastSuccess > STALE_FEED_MS;
}
