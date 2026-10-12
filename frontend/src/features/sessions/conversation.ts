/**
 * Pulls the human-readable exchange out of one trace ("turn") for the session
 * conversation view. Pure functions only — no React here.
 */
import { messageText, parseChatPayload, type ChatMessage } from "@/features/traces";
import type { Span } from "@/lib/api";

export interface TurnConversation {
  /** The user's message that started this turn. */
  userText: string | null;
  /** The assistant's final reply. */
  assistantText: string | null;
}

function startTime(span: Span): number {
  return new Date(span.started_at).getTime();
}

function byStartTime(a: Span, b: Span): number {
  return startTime(a) - startTime(b);
}

function lastTextFrom(messages: ChatMessage[] | null, role: ChatMessage["role"]): string | null {
  if (messages === null) {
    return null;
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === role) {
      const text = messageText(message);
      if (text !== null) {
        return text;
      }
    }
  }
  return null;
}

function plainText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/**
 * The user's text is the last user message with text in the *first* LLM call
 * (later calls in an agent loop end with tool results, not the question).
 * The assistant's text comes from the *last* LLM call that produced output.
 * Traces without chat-shaped LLM spans fall back to plain-string payloads on
 * the root span.
 */
export function extractTurnConversation(spans: readonly Span[]): TurnConversation {
  const llmSpans = spans.filter((span) => span.kind === "llm").sort(byStartTime);

  let userText: string | null = null;
  for (const span of llmSpans) {
    userText = lastTextFrom(parseChatPayload(span.input), "user") ?? plainText(span.input);
    if (userText !== null) {
      break;
    }
  }

  // Only the final LLM call that produced output can hold the answer. If it
  // has no text (failed, or tool calls only), an earlier call — a classifier,
  // say — would be misleading, so fall through to the root span instead.
  let assistantText: string | null = null;
  const finalCall = llmSpans.findLast((span) => span.output !== null && span.output !== undefined);
  if (finalCall) {
    assistantText =
      lastTextFrom(parseChatPayload(finalCall.output), "assistant") ?? plainText(finalCall.output);
  }

  if (userText === null || assistantText === null) {
    const spanIds = new Set(spans.map((span) => span.span_id));
    const root = [...spans]
      .sort(byStartTime)
      .find((span) => span.parent_span_id === null || !spanIds.has(span.parent_span_id));
    if (root) {
      userText ??= lastTextFrom(parseChatPayload(root.input), "user") ?? plainText(root.input);
      assistantText ??=
        lastTextFrom(parseChatPayload(root.output), "assistant") ?? plainText(root.output);
    }
  }

  return { userText, assistantText };
}

/** The first span that failed, by start time; its message is the trace's `error_message`. */
export function earliestFailedSpan(spans: readonly Span[]): Span | null {
  const failed = spans.filter((span) => span.status === "error").sort(byStartTime);
  return failed[0] ?? null;
}
