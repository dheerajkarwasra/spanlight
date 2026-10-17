/** A metadata key as the server accepts it (program contract §3.1). */
export const METADATA_KEY_PATTERN = /^[a-z0-9_.-]{1,64}$/;

/** A stored value's limit, in code points: a value of 256 emoji is valid. */
export const METADATA_VALUE_MAX = 256;

/** What the server stores in place of a value that looked like a secret. */
export const REDACTED = "[REDACTED]";

export type MetadataPair = [key: string, value: string];

/** The filter as one phrase: `customer_id = acme`, or `No customer_id` for the key alone. */
export function metadataFilterLabel(key: string, value: string | undefined): string {
  return value === undefined ? `No ${key}` : `${key} = ${value}`;
}

export function isMetadataValueTooLong(value: string): boolean {
  // Code points on purpose, as the server counts: not graphemes, not UTF-16 units.
  return Array.from(value).length > METADATA_VALUE_MAX;
}

/**
 * The keys the filter can pick: the promoted ones first, then the ones seen in the last 7 days,
 * plus a key from the URL that neither list has (an attribution drill-down of an older key).
 */
export function metadataKeyOptions(
  promoted: readonly string[],
  seen: readonly string[],
  current?: string,
): string[] {
  const keys = [...new Set([...promoted, ...seen])];
  return current !== undefined && !keys.includes(current) ? [current, ...keys] : keys;
}

/** The promoted keys the trace carries, in the project's promoted order. */
export function promotedPairs(
  metadata: Readonly<Record<string, string>>,
  promoted: readonly string[],
): MetadataPair[] {
  // `hasOwn`: `constructor` and `__proto__` are valid keys, and `in` would find them on every object.
  return promoted.flatMap((key) =>
    Object.hasOwn(metadata, key) ? [[key, metadata[key]] as MetadataPair] : [],
  );
}

/** Every pair, by key. */
export function sortedPairs(metadata: Readonly<Record<string, string>>): MetadataPair[] {
  return Object.entries(metadata).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}
