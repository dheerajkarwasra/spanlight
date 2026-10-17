import type { FallbackCondition } from "@/lib/api";

/** Spec §9.1 "Fallback options". */
export const FALLBACK_LABELS: Record<FallbackCondition, string> = {
  status_5xx: "Provider 5xx errors",
  rate_limited: "Rate limited (429)",
  timeout: "Timeouts",
  connection_error: "Connection errors",
  auth_error: "Auth errors (401/403)",
};

const FIELD_LABELS: Record<string, string> = {
  "retry.max_attempts": "Max attempts",
  "retry.backoff_ms": "Backoff",
  "retry.max_backoff_ms": "Max backoff",
  "retry.honour_retry_after": "Honour Retry-After",
  "retry.on_statuses": "Retry on statuses",
  "fallback.on": "Fallback conditions",
  timeout_ms: "Timeout",
  targets: "Targets",
};

const TARGET_FIELD_LABELS: Record<string, string> = {
  credential_id: "Credential",
  weight: "Weight",
  aliases: "Model aliases",
  model_aliases: "Model aliases",
};

/**
 * A friendly name for a config or form path, e.g. `targets.0.weight` → "Weight (target 1)" and
 * `retry.on_statuses` → "Retry on statuses". Used by the version diff and the save bar; the raw
 * path stays visible next to it where the person needs to match it to the API.
 */
export function fieldLabel(path: string): string {
  const known = FIELD_LABELS[path];
  if (known) {
    return known;
  }
  const match = /^targets\.(\d+)(?:\.([a-z_]+))?/.exec(path);
  if (match) {
    const position = Number(match[1]) + 1;
    const field = match[2];
    if (field === undefined) {
      return `Target ${position}`;
    }
    return `${TARGET_FIELD_LABELS[field] ?? field} (target ${position})`;
  }
  return path;
}

/** "a", "a and b", "a, b and c". */
export function joinWords(words: readonly string[]): string {
  if (words.length <= 1) {
    return words.join("");
  }
  return `${words.slice(0, -1).join(", ")} and ${words.at(-1) ?? ""}`;
}
