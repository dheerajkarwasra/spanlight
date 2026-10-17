import { NO_PRICE_REASON } from "@/components/cost-value";
import type { Span } from "@/lib/api";

import { usageReason } from "./usage-state-copy";

/**
 * Why a span's usage or cost is unknown, in the words of the span itself. A count that never came
 * is not a missing price. A stored cut-stream reason is the most specific and wins; otherwise the
 * server's `usage_state` picks the copy (`usage-state-copy.ts`), and the neutral sentences below
 * cover the states that explain nothing.
 */

/** A token count nobody reported, whatever recorded the span. */
export const TOKENS_NOT_REPORTED = "Not reported for this call";

/**
 * Each cut-stream `usage.unreported_reason`, worded as the server words it (`explain_copy.py`).
 * `not_exported` is left out: its `usage_not_reported` state already words it.
 */
const UNREPORTED_SENTENCES: Record<string, string> = {
  client_disconnected: "the client disconnected",
  stream_error: "the stream failed",
  stream_cut_by_fault: "a Lab fault cut the stream",
  include_usage_not_set: "stream_options.include_usage was not set",
  no_final_usage: "the provider sent no final usage event",
};

type UsageFields = Pick<
  Span,
  "model" | "input_tokens" | "output_tokens" | "attributes" | "usage_state"
>;

/** The stored reason the output count is unknown, in words; null when none (or an unknown one). */
function unreportedSentence(span: Pick<Span, "attributes">): string | null {
  const reason = span.attributes["usage.unreported_reason"];
  // `hasOwn`: an attribute is free text, and "constructor" is a key of every object.
  return typeof reason === "string" && Object.hasOwn(UNREPORTED_SENTENCES, reason)
    ? (UNREPORTED_SENTENCES[reason] ?? null)
    : null;
}

/** The tooltip of an unknown input or output token count. */
export function tokenUnknownReason(
  span: Pick<Span, "attributes" | "usage_state">,
  field: "input" | "output",
): string {
  const sentence = unreportedSentence(span);
  if (sentence !== null) {
    return `Not reported: ${sentence}`;
  }
  return usageReason(span.usage_state, field) ?? TOKENS_NOT_REPORTED;
}

/** The tooltip of an unknown span cost: no model call, unreported usage, or no price. */
export function costUnknownReason(span: UsageFields): string {
  const stateReason = usageReason(span.usage_state, "cost");
  if (stateReason !== null && unreportedSentence(span) === null) {
    return stateReason;
  }
  if (span.model === null) {
    return "No model call on this span";
  }
  if (span.input_tokens === null) {
    return "Unknown: usage not reported for this call";
  }
  if (span.output_tokens === null) {
    const sentence = unreportedSentence(span);
    return sentence === null
      ? "Unknown: output tokens not reported for this call"
      : `Unknown: output tokens not reported (${sentence})`;
  }
  return NO_PRICE_REASON;
}
