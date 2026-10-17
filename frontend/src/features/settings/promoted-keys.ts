/** The metadata key pattern the server enforces (program contract §3.1). */
export const METADATA_KEY_PATTERN = /^[a-z0-9_.-]{1,64}$/;

/** At most this many keys get a daily rollup (spec §5.1). */
export const MAX_PROMOTED_KEYS = 5;

export const KEY_PATTERN_MESSAGE =
  "Use lower-case letters, digits, dots, dashes or underscores, up to 64 characters.";

/** Why `key` can't be promoted, or null when it is a valid metadata key. */
export function keyProblem(key: string): string | null {
  return METADATA_KEY_PATTERN.test(key) ? null : KEY_PATTERN_MESSAGE;
}

/** `keys` with `key` appended, unless it is already there or the list is full. */
export function addKey(keys: readonly string[], key: string): string[] {
  return keys.includes(key) || keys.length >= MAX_PROMOTED_KEYS ? [...keys] : [...keys, key];
}
