/**
 * Stable hash of a model request, used to group identical calls.
 *
 * Byte-for-byte identical to the server and Python SDK (`_request_hash.py`): the first 32 hex
 * characters of SHA-256 of the canonical JSON (sorted keys, `,` and `:` separators) of
 * `{"model": model, "input": cleaned}`. `cleaned` drops top-level transport/caller keys.
 */

import { createHash } from "node:crypto";

const IGNORED_INPUT_KEYS: ReadonlySet<string> = new Set([
  "stream",
  "stream_options",
  "user",
  "metadata",
  "idempotency_key",
  "extra_headers",
  "extra_query",
  "extra_body",
  "timeout",
  "api_key",
  "http_client",
]);

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      const item = obj[key];
      if (item !== undefined) out[key] = sortKeys(item);
    }
    return out;
  }
  return value;
}

/** Return the 32-hex request hash, or undefined when `value` is null or undefined. */
export function requestHash(model: string | undefined, value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  let cleaned = value;
  if (typeof value === "object" && !Array.isArray(value)) {
    const filtered: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (!IGNORED_INPUT_KEYS.has(key) && item !== undefined) filtered[key] = item;
    }
    cleaned = filtered;
  }
  const canonical = JSON.stringify(sortKeys({ model: model ?? null, input: cleaned }));
  return createHash("sha256").update(canonical, "utf8").digest("hex").slice(0, 32);
}
