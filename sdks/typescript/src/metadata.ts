/**
 * Trace metadata rules on the client: lenient validator and merge. Pure.
 *
 * Mirrors `sdks/python/src/spanlight/_metadata.py` and the shared `sdks/fixtures/metadata-cases.json`
 * contract: at most 32 keys matching `^[a-z0-9_.-]{1,64}$`, values 1..256 code points, finite
 * numbers/booleans serialized via JSON text, null/undefined dropped.
 */

import { log } from "./log.js";

export const MAX_METADATA_KEYS = 32;
export const MAX_METADATA_VALUE = 256;
const KEY_PATTERN = /^[a-z0-9_.-]{1,64}$/;
const SHOWN_KEY = 64;

function shown(key: unknown): string {
  const text = typeof key === "string" ? key : JSON.stringify(key);
  if (text.length <= SHOWN_KEY) return JSON.stringify(text);
  return JSON.stringify(`${text.slice(0, SHOWN_KEY)}...`);
}

function codePointLength(value: string): number {
  let count = 0;
  for (let i = 0; i < value.length;) {
    const code = value.codePointAt(i) ?? 0;
    i += code > 0xffff ? 2 : 1;
    count += 1;
  }
  return count;
}

function valueText(value: unknown): string | undefined {
  let text: string;
  if (typeof value === "string") {
    text = value;
  } else if (typeof value === "number") {
    if (!Number.isFinite(value)) return undefined;
    // Match Python's json.dumps(float/int) formatting (`1000.0` vs `1000` isn't distinguished in JS,
    // all finite numbers serialize canonically with JSON.stringify).
    text = JSON.stringify(value);
  } else if (typeof value === "boolean") {
    text = value ? "true" : "false";
  } else {
    return undefined;
  }
  const len = codePointLength(text);
  return len >= 1 && len <= MAX_METADATA_VALUE ? text : undefined;
}

/** Return the valid entries of `raw` according to Spanlight's metadata rules. Never throws. */
export function cleanMetadata(raw: unknown): Record<string, string> {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== "object" || Array.isArray(raw)) {
    log.debug(`updateTrace(metadata) needs a plain object, got ${typeof raw}; ignoring`);
    return {};
  }
  const valid: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (value === null || value === undefined) continue;
    if (!KEY_PATTERN.test(key)) {
      log.debug(`Dropping trace metadata key ${shown(key)}: invalid key`);
      continue;
    }
    const text = valueText(value);
    if (text === undefined) {
      log.debug(`Dropping trace metadata key ${shown(key)}: invalid value`);
      continue;
    }
    valid[key] = text;
  }
  const keys = Object.keys(valid).sort();
  if (keys.length <= MAX_METADATA_KEYS) return valid;
  const out: Record<string, string> = {};
  for (const key of keys.slice(0, MAX_METADATA_KEYS)) {
    const entry = valid[key];
    if (entry !== undefined) out[key] = entry;
  }
  for (const key of keys.slice(MAX_METADATA_KEYS)) {
    log.debug(`Dropping trace metadata key ${shown(key)}: at most ${MAX_METADATA_KEYS} keys`);
  }
  return out;
}

/** Merge `incoming` into `current` keeping existing keys updatable and capping total keys at 32. */
export function mergeMetadata(
  current: Readonly<Record<string, string>>,
  incoming: Readonly<Record<string, string>>,
): Record<string, string> {
  const merged: Record<string, string> = { ...current };
  for (const [key, value] of Object.entries(incoming)) {
    if (Object.hasOwn(current, key)) merged[key] = value;
  }
  const room = Math.max(0, MAX_METADATA_KEYS - Object.keys(current).length);
  const newKeys = Object.keys(incoming)
    .filter((key) => !Object.hasOwn(current, key))
    .sort();
  for (const key of newKeys.slice(0, room)) {
    const entry = incoming[key];
    if (entry !== undefined) merged[key] = entry;
  }
  for (const key of newKeys.slice(room)) {
    log.debug(`Dropping trace metadata key ${shown(key)}: the trace has ${MAX_METADATA_KEYS} keys`);
  }
  return merged;
}
