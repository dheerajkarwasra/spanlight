/**
 * Decides how a span input/output payload is shown. Pure functions only — no React here.
 */
import { parseChatPayload, type ChatMessage } from "./chat-messages";

/**
 * The ingestion API replaces an `input`/`output` over 1 MiB with a string holding the first
 * 1 MiB of its JSON text and flags the span `truncated` (docs/api-deviations.md).
 */
export const PAYLOAD_LIMIT_BYTES = 1024 * 1024;

/** A multi-byte UTF-8 character split by the cut is dropped, losing up to 3 bytes. */
const MAX_BYTES_LOST_AT_CUT = 3;

export type PayloadContent =
  | { format: "empty" }
  | { format: "chat"; messages: ChatMessage[] }
  | { format: "text"; text: string }
  | { format: "json"; value: unknown };

export function classifyPayload(value: unknown): PayloadContent {
  if (value === null || value === undefined) {
    return { format: "empty" };
  }
  const messages = parseChatPayload(value);
  if (messages) {
    return { format: "chat", messages };
  }
  if (typeof value === "string") {
    return { format: "text", text: value };
  }
  return { format: "json", value };
}

/** The muted hint next to a section title: "2 messages", "Text" or "JSON". */
export function payloadHint(content: PayloadContent): string | null {
  switch (content.format) {
    case "empty":
      return null;
    case "chat":
      return content.messages.length === 1 ? "1 message" : `${content.messages.length} messages`;
    case "text":
      return "Text";
    case "json":
      return "JSON";
  }
}

/**
 * Whether this payload is the one the server cut. Only the oversized side of a truncated span
 * is a string of (almost exactly) the payload limit.
 */
export function isCutPayload(value: unknown, spanTruncated: boolean): boolean {
  if (!spanTruncated || typeof value !== "string") {
    return false;
  }
  const bytes = new TextEncoder().encode(value).length;
  return bytes >= PAYLOAD_LIMIT_BYTES - MAX_BYTES_LOST_AT_CUT;
}
