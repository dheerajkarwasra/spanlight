/**
 * Rate-limit response headers a span may keep, and the snapshot filter. Pure.
 *
 * Mirrors `sdks/python/src/spanlight/_ratelimit_headers.py` (exact 29-header allowlist).
 */

export const RATELIMIT_HEADERS: ReadonlySet<string> = new Set([
  "retry-after",
  "retry-after-ms",
  "x-ratelimit-limit-requests",
  "x-ratelimit-limit-tokens",
  "x-ratelimit-remaining-requests",
  "x-ratelimit-remaining-tokens",
  "x-ratelimit-reset-requests",
  "x-ratelimit-reset-tokens",
  "x-ratelimit-limit-project-tokens",
  "x-ratelimit-remaining-project-tokens",
  "x-ratelimit-reset-project-tokens",
  "anthropic-ratelimit-requests-limit",
  "anthropic-ratelimit-requests-remaining",
  "anthropic-ratelimit-requests-reset",
  "anthropic-ratelimit-tokens-limit",
  "anthropic-ratelimit-tokens-remaining",
  "anthropic-ratelimit-tokens-reset",
  "anthropic-ratelimit-input-tokens-limit",
  "anthropic-ratelimit-input-tokens-remaining",
  "anthropic-ratelimit-input-tokens-reset",
  "anthropic-ratelimit-output-tokens-limit",
  "anthropic-ratelimit-output-tokens-remaining",
  "anthropic-ratelimit-output-tokens-reset",
  "anthropic-priority-input-tokens-limit",
  "anthropic-priority-input-tokens-remaining",
  "anthropic-priority-input-tokens-reset",
  "anthropic-priority-output-tokens-limit",
  "anthropic-priority-output-tokens-remaining",
  "anthropic-priority-output-tokens-reset",
]);

export const MAX_HEADER_VALUE_CHARS = 64;

function isAscii(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    if (text.charCodeAt(i) > 0x7f) return false;
  }
  return true;
}

function isValidValue(value: unknown): value is string {
  if (typeof value !== "string" || value.length > MAX_HEADER_VALUE_CHARS) return false;
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code < 0x20 || code > 0x7e) return false;
  }
  return true;
}

function isHeadroom(name: string): boolean {
  return (
    name.endsWith("-limit") ||
    name.endsWith("-remaining") ||
    name.startsWith("x-ratelimit-limit-") ||
    name.startsWith("x-ratelimit-remaining-")
  );
}

export type HeaderSource = Headers | Readonly<Record<string, unknown>>;

/** Iterate `[lowercaseName, value]` pairs from a Web `Headers` object or plain header map. */
export function headerEntries(headers: HeaderSource): [string, unknown][] {
  if (typeof Headers !== "undefined" && headers instanceof Headers) {
    const out: [string, unknown][] = [];
    headers.forEach((value, name) => {
      if (isAscii(name)) out.push([name.toLowerCase(), value]);
    });
    return out;
  }
  const out: [string, unknown][] = [];
  for (const [name, value] of Object.entries(headers as Record<string, unknown>)) {
    if (isAscii(name)) out.push([name.toLowerCase(), value]);
  }
  return out;
}

/** Return the allowlisted rate-limit headers from `headers`, lowercase-keyed. */
export function snapshotHeaders(
  headers: HeaderSource | undefined | null,
  options: { failed?: boolean } = {},
): Record<string, string> {
  if (headers === undefined || headers === null || typeof headers !== "object") return {};
  const failed = options.failed ?? true;
  const snapshot: Record<string, string> = {};
  for (const [name, value] of headerEntries(headers)) {
    if (!RATELIMIT_HEADERS.has(name) || !isValidValue(value)) continue;
    if (!failed && !isHeadroom(name)) continue;
    snapshot[name] = value;
  }
  return snapshot;
}
