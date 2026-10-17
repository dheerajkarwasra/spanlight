import type { AttributionGroupBy } from "@/lib/api";

/** Windows up to this long read raw spans for any key; longer ones read the promoted-key rollup. */
const RAW_WINDOW_HOURS = 24;

/**
 * Whether the attribution request must not be sent: metadata needs a key, and a key that is not
 * promoted has no rollup to answer a window over 24 hours (the API would answer 422).
 */
export function isFetchDisabled(
  groupBy: AttributionGroupBy,
  key: string | undefined,
  promoted: readonly string[],
  windowHours: number,
): boolean {
  if (groupBy !== "metadata") {
    return false;
  }
  if (key === undefined) {
    return true;
  }
  return windowHours > RAW_WINDOW_HOURS && !promoted.includes(key);
}
