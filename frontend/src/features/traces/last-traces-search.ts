import type { TraceFilters } from "./search";

/**
 * The traces list's search params from the last visit, so "Back to traces"
 * on the detail page returns to the same filtered view, and the trace that
 * was opened last, so the list can highlight the row you came back from.
 * In memory only: a reload starts from a clean list, which is the expected
 * behaviour.
 */
let lastSearch: TraceFilters | null = null;
let lastOpenedTraceId: string | null = null;

export function rememberTracesSearch(search: TraceFilters): void {
  // "Back to traces" returns to the list, not to the peek it had open.
  const { peek: _peek, ...list } = search;
  lastSearch = list;
}

export function recallTracesSearch(): TraceFilters {
  return lastSearch ?? {};
}

export function rememberOpenedTrace(traceId: string): void {
  lastOpenedTraceId = traceId;
}

export function recallOpenedTrace(): string | null {
  return lastOpenedTraceId;
}
