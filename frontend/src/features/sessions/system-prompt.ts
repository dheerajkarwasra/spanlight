/** The system prompt of a session turn and whether it moved since turn 1. Pure functions only. */
import { messageText, parseChatPayload } from "@/features/traces";
import type { Span } from "@/lib/api";

/**
 * The system prompt of the turn's first LLM call with an input: the Anthropic `system` field or the
 * text of its `role: "system"` messages, joined. Null when that call has none or wasn't captured.
 */
export function systemPromptOf(spans: readonly Span[]): string | null {
  const first = spans
    .filter((span) => span.kind === "llm" && span.input !== null && span.input !== undefined)
    .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))[0];
  const texts = (parseChatPayload(first?.input) ?? [])
    .filter((message) => message.role === "system")
    .map(messageText)
    .filter((text): text is string => text !== null);
  return texts.length > 0 ? texts.join("\n\n") : null;
}

/** Turn N's system prompt against turn 1's; null when either is unknown. */
export function promptChange(
  first: string | null,
  current: string | null,
): "unchanged" | "changed" | null {
  if (first === null || current === null) return null;
  return first.trim() === current.trim() ? "unchanged" : "changed";
}
