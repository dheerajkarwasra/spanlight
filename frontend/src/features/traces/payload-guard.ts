/**
 * Size guard for span payloads: a serialized payload over `GUARD_BYTES` renders only its first
 * `PREVIEW_BYTES` until the user asks for all of it. Pure functions only — no React here.
 */

export const GUARD_BYTES = 64 * 1024;
export const PREVIEW_BYTES = 16 * 1024;

export interface GuardedPayload {
  truncated: boolean;
  shown: string;
  /** UTF-8 bytes of the whole serialized payload. */
  totalBytes: number;
}

export function guardPayload(serialized: string): GuardedPayload {
  const bytes = new TextEncoder().encode(serialized);
  if (bytes.length <= GUARD_BYTES) {
    return { truncated: false, shown: serialized, totalBytes: bytes.length };
  }
  // `stream: true` holds back a multi-byte character cut by the slice instead of emitting U+FFFD.
  const shown = new TextDecoder().decode(bytes.subarray(0, PREVIEW_BYTES), { stream: true });
  return { truncated: true, shown, totalBytes: bytes.length };
}

/** "16 KB", "1.2 MB": binary units, as the guard counts them. */
export function formatPayloadBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
