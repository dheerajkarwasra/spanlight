import type { ReleaseStats } from "@/lib/api";

export interface ReleasePair {
  a: string | undefined;
  b: string | undefined;
  /** True when the pair comes from the list, not the URL. */
  derived: boolean;
}

/**
 * The releases to compare: the URL's pair wins; without one, the previous release against the
 * latest. The list is newest first (`last_seen_at DESC`), so `list[1]` is the previous one.
 */
export function defaultPair(list: readonly ReleaseStats[], a?: string, b?: string): ReleasePair {
  const [latest, previous] = list;
  if (a !== undefined || b !== undefined || latest === undefined || previous === undefined) {
    return { a, b, derived: false };
  }
  return { a: previous.release, b: latest.release, derived: true };
}
