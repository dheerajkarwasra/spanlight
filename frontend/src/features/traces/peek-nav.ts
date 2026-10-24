/**
 * The trace `delta` rows from `current` in the loaded list: null at either end (no wrap, no
 * "Load more") and when `current` has left the list (filters changed, the list refetched).
 */
export function stepPeek(ids: readonly string[], current: string, delta: 1 | -1): string | null {
  const index = ids.indexOf(current);
  return index < 0 ? null : (ids[index + delta] ?? null);
}
