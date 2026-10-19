import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { projectsApi, queryKeys, type MetricsQuery, type TraceListQuery } from "@/lib/api";

import type { TraceFilters } from "./search";

export const TRACE_PAGE_SIZE = 50;

/** Maps URL search params onto the list endpoint's query parameters. */
export function toTraceListQuery(
  search: TraceFilters,
  window: { from: string; to: string },
  environment: string | undefined,
): Omit<TraceListQuery, "cursor"> {
  // A range or value needs its score name (the API refuses it alone), so a URL without one sends
  // neither.
  const named = search.score !== undefined && search.score !== "";
  return {
    from: window.from,
    to: window.to,
    environment,
    release: search.release,
    model: search.model,
    status: search.status,
    error_class: search.error_class,
    user_id: search.user,
    session_id: search.session,
    tag: search.tag,
    q: search.q,
    score_name: search.score,
    score_min: named ? search.score_min : undefined,
    score_max: named ? search.score_max : undefined,
    score_value: named ? search.score_value : undefined,
    has_scores: search.has_scores,
    prompt_version_id: search.prompt_version,
    has_retries: search.has_retries,
    metadata_key: search.metadata_key,
    // A value alone is a 422; the URL can carry one, the list never sends it.
    metadata_value: search.metadata_key !== undefined ? search.metadata_value : undefined,
    name: search.name,
    sort: search.sort,
    limit: TRACE_PAGE_SIZE,
  };
}

/** Cursor-paginated trace list. Previous results stay on screen while filters change. */
export function useTraceListQuery(search: TraceFilters) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = toTraceListQuery(search, range, environment);

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).traces(query),
    queryFn: ({ pageParam }) => projectsApi.traces(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/**
 * How many traces started in the current window and environment: the overview KPI. Shares its
 * cache entry with the overview page, so switching between the two pages doesn't refetch.
 */
export function useTraceCountQuery() {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const metricsWindow: MetricsQuery = { from: range.from, to: range.to, environment };

  return useQuery({
    queryKey: queryKeys.project(projectId).overview(metricsWindow),
    queryFn: () => projectsApi.overview(projectId, metricsWindow),
    select: (overview) => overview.current.traces,
  });
}

/** Two traces step by step; `enabled` once both ids are known and differ. */
export function useTraceComparisonQuery(a: string | undefined, b: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).traceComparison(a ?? "", b ?? ""),
    queryFn: () => projectsApi.compareTraces(projectId, a ?? "", b ?? ""),
    staleTime: 60_000,
    enabled: a !== undefined && b !== undefined && a !== b,
  });
}

/** One trace with all of its spans. Trace data rarely changes once ingested. */
export function useTraceQuery(traceId: string, options: { enabled?: boolean } = {}) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).trace(traceId),
    queryFn: () => projectsApi.trace(projectId, traceId),
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}
