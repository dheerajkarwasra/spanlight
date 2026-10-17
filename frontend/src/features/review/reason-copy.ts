import { insightKindLabel } from "@/features/doctor/insight-format";
import type { QueueItem, ReviewReason, TraceDetail } from "@/lib/api";
import { pluralize } from "@/lib/format";

const REASON_LABELS: Record<ReviewReason, string> = {
  doctor_finding: "Doctor finding",
  error: "Error",
  cost_outlier: "Cost outlier",
  random: "Random",
  manual: "Manual",
};

/** Every reason, in the selection's priority order (manual last). */
export const REVIEW_REASONS = Object.keys(REASON_LABELS) as [ReviewReason, ...ReviewReason[]];

/** The chip text of a reason: "Doctor finding", "Cost outlier", … */
export function reasonLabel(reason: ReviewReason): string {
  return REASON_LABELS[reason];
}

type Spans = Pick<TraceDetail, "spans">;

/** "$0.4213": the API's 4-place decimal string as dollars. */
function dollars(value: string | undefined): string {
  return value === undefined ? "an unknown amount" : `$${value}`;
}

/** "1%", "0.5%", "1.25%" from a 4-place rate string; null when it is not a number. */
function percent(rate: string | undefined): string | null {
  const value = Number(rate);
  return rate === undefined || !Number.isFinite(value)
    ? null
    : `${String(Number((value * 100).toFixed(2)))}%`;
}

function errorSentence(item: QueueItem, trace: Spans | null): string {
  const detail = item.reason_detail ?? {};
  const failed =
    detail.error_count === undefined
      ? "failed spans (count unknown)"
      : pluralize(detail.error_count, "failed span");
  const spanId = detail.first_error_span_id ?? null;
  if (spanId === null) {
    return `error — ${failed}, first span unknown`;
  }
  const name = trace?.spans.find((span) => span.span_id === spanId)?.name ?? spanId;
  const errorClass = detail.error_class ? ` (${detail.error_class})` : "";
  return `error — ${failed}, first: \`${name}\`${errorClass}`;
}

function costOutlierSentence(item: QueueItem): string {
  const detail = item.reason_detail ?? {};
  const name =
    detail.trace_name === undefined ? "an unknown trace name" : `\`${detail.trace_name}\``;
  const sample =
    detail.sample === undefined ? "sample size unknown" : pluralize(detail.sample, "trace");
  return (
    `cost outlier — ${dollars(detail.cost_usd)} against a p99 of ${dollars(detail.p99_usd)} ` +
    `for ${name} (${sample}, 7 days)`
  );
}

/**
 * The banner's account of why the trace is in the queue, after "Why this trace: ". Span and trace
 * names are wrapped in backticks for the banner to set as code. The first failed span is named
 * from `trace` when given and found, else by its id. A part the selection did not record says it
 * is unknown.
 */
export function reasonSentence(item: QueueItem, trace: Spans | null = null): string {
  const detail = item.reason_detail ?? {};
  switch (item.reason ?? "manual") {
    case "doctor_finding":
      return detail.kind === undefined
        ? "open Doctor finding (kind unknown)"
        : `open Doctor finding: ${insightKindLabel(detail.kind)}`;
    case "error":
      return errorSentence(item, trace);
    case "cost_outlier":
      return costOutlierSentence(item);
    case "random":
      return `random sample (${percent(detail.rate) ?? "rate unknown"})`;
    case "manual":
      return "added by hand";
  }
}

/**
 * The span "Replay failing span" opens: the item's first failed span when the trace has it, else
 * the trace's last LLM call, else null.
 */
export function replaySpanOf(item: QueueItem, trace: Spans): string | null {
  const errorSpanId = item.reason_detail?.first_error_span_id ?? null;
  if (errorSpanId !== null && trace.spans.some((span) => span.span_id === errorSpanId)) {
    return errorSpanId;
  }
  const llm = trace.spans
    .filter((span) => span.kind === "llm")
    .sort((a, b) => a.started_at.localeCompare(b.started_at));
  return llm.at(-1)?.span_id ?? null;
}
