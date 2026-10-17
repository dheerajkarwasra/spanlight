import type { QueueItem, Span, TraceDetail } from "@/lib/api";

import { replaySpanOf } from "./reason-copy";

/** The Phase 5 line every "save as dataset item" surface repeats. */
export const RECORDED_NOTE =
  "Edit the expected output; the recorded output is only a starting point.";

/** Why the item was kept, as the dataset item's `metadata.review`. */
export interface ReviewMark {
  queue_id: string;
  item_id: string;
  reason: NonNullable<QueueItem["reason"]>;
  /** The reviewer's current `failure_category` label; null when none is picked yet. */
  failure_category: string | null;
}

/** The span a review save copies, and how the dialog names the choice. */
export interface SaveSpan {
  span: Span;
  which: "reviewed span" | "first failed span" | "last LLM span";
}

/**
 * The span "Save as dataset item" copies: the item's own span, else the span "Replay failing
 * span" opens (the first failed span, else the last LLM call). Null when the trace has none.
 */
export function saveSpanOf(item: QueueItem, trace: Pick<TraceDetail, "spans">): SaveSpan | null {
  const spanId = item.span_id ?? replaySpanOf(item, trace);
  const span = trace.spans.find((candidate) => candidate.span_id === spanId);
  if (span === undefined) {
    return null;
  }
  const which =
    item.span_id !== null
      ? "reviewed span"
      : span.span_id === item.reason_detail?.first_error_span_id
        ? "first failed span"
        : "last LLM span";
  return { span, which };
}

/**
 * The copy's metadata with `review` added. The base is what the from-traces copy stores
 * (`items_from_traces` in the backend): the PATCH replaces metadata, so it is sent again whole.
 */
export function reviewMetadata(
  trace: Pick<TraceDetail, "trace_id" | "environment">,
  span: Pick<Span, "span_id" | "model">,
  mark: ReviewMark,
): Record<string, unknown> {
  return {
    trace_id: trace.trace_id,
    span_id: span.span_id,
    model: span.model,
    environment: trace.environment,
    review: mark,
  };
}
