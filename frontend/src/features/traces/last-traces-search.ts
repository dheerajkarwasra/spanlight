import { useMe } from "@/features/auth";
import { useProjectParams } from "@/features/shell/project-context";

import type { TraceFilters } from "./search";

/**
 * The traces list's search params from the last visit, so "Back to traces"
 * on the detail page returns to the same filtered view, and the trace that
 * was opened last, so the list can highlight the row you came back from.
 * In memory only: a reload starts from a clean list, which is the expected
 * behaviour.
 */
let lastScope: string | null = null;
let lastSearch: TraceFilters | null = null;
let lastOpenedTraceId: string | null = null;

export function useTraceHistoryScope(): string {
  const { user } = useMe();
  const { orgId, projectId } = useProjectParams();
  return JSON.stringify([user.id, orgId, projectId]);
}

export function clearTraceHistory(): void {
  lastScope = null;
  lastSearch = null;
  lastOpenedTraceId = null;
}

function enterScope(scope: string): void {
  if (scope !== lastScope) {
    lastScope = scope;
    lastSearch = null;
    lastOpenedTraceId = null;
  }
}

export function rememberTracesSearch(search: TraceFilters, scope: string): void {
  enterScope(scope);
  // "Back to traces" returns to the list, not to the peek it had open.
  const { peek: _peek, ...list } = search;
  lastSearch = list;
}

export function recallTracesSearch(scope: string): TraceFilters {
  enterScope(scope);
  return lastSearch ?? {};
}

export function rememberOpenedTrace(traceId: string, scope: string): void {
  enterScope(scope);
  lastOpenedTraceId = traceId;
}

export function recallOpenedTrace(scope: string): string | null {
  enterScope(scope);
  return lastOpenedTraceId;
}
