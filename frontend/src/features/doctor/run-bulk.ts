export interface BulkResult {
  ok: string[];
  failed: { id: string; error: unknown }[];
}

/**
 * Runs `action` once per id, at most `concurrency` at a time, and never stops on a failure:
 * every id ends up in `ok` or in `failed` with its error (spec ruling 16, no bulk endpoint).
 */
export async function runBulk<T>(
  ids: string[],
  action: (id: string) => Promise<T>,
  concurrency = 4,
): Promise<BulkResult> {
  const result: BulkResult = { ok: [], failed: [] };
  let next = 0;
  async function worker() {
    while (next < ids.length) {
      const id = ids[next] ?? "";
      next += 1;
      try {
        await action(id);
        result.ok.push(id);
      } catch (error) {
        result.failed.push({ id, error });
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, ids.length) }, worker));
  // In the order given, not the order the requests finished.
  result.ok.sort((a, b) => ids.indexOf(a) - ids.indexOf(b));
  result.failed.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  return result;
}
