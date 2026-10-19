import type { Span } from "@/lib/api";

/** What a trace is added to a dataset or queue by: one trace, or several from the list. */
export interface TraceRef {
  trace_id: string;
  name: string | null;
}

/**
 * The span the server saves when no span is named: the first `llm` span by start time (ties by
 * span id), as `items_from_traces` picks it.
 */
export function firstLlmSpan(spans: readonly Span[]): Span | null {
  let first: Span | null = null;
  for (const span of spans) {
    if (span.kind !== "llm") continue;
    const order = first === null ? -1 : Date.parse(span.started_at) - Date.parse(first.started_at);
    if (order < 0 || (order === 0 && first !== null && span.span_id < first.span_id)) {
      first = span;
    }
  }
  return first;
}

/** Whether the span has a stored input: without one the server rejects it, `no payload stored`. */
export function hasPayload(span: Span): boolean {
  // An omitted payload is stored; trace detail only left it out of the response.
  return span.payload_omitted || (span.input !== null && span.input !== undefined);
}

/** "lookup_order · tool span". */
export function spanLabel(span: Span): string {
  return `${span.name} · ${span.kind} span`;
}
