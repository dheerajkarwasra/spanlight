import { keepPreviousData, useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";

import { useProjectInsights, type InsightQuery } from "@/features/doctor";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import {
  projectsApi,
  type Insight,
  queryKeys,
  type MetricsQuery,
  type Span,
  type TraceListQuery,
} from "@/lib/api";

import { fillBuckets } from "./histogram";
import type { TraceFilters } from "./search";

export const TRACE_PAGE_SIZE = 50;

/**
 * Maps URL search params onto the list endpoint's query parameters. `preview` asks for the
 * input/output previews, which cost the server a payload read, so only while a column shows them.
 */
export function toTraceListQuery(
  search: TraceFilters,
  window: { from: string; to: string },
  environment: string | undefined,
  preview = false,
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
    order: search.order,
    preview: preview || undefined,
    limit: TRACE_PAGE_SIZE,
  };
}

/** Cursor-paginated trace list. Previous results stay on screen while filters change. */
export function useTraceListQuery(search: TraceFilters, preview = false) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = toTraceListQuery(search, range, environment, preview);

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).traces(query),
    queryFn: ({ pageParam }) => projectsApi.traces(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/**
 * Traces and errors per bucket for the list's filters, gaps filled across the window the request
 * asked for (no bars when the window has no trace). Its own request, so the table never waits for
 * it; the previous bars stay while the filters change.
 */
export function useTraceHistogramQuery(search: TraceFilters) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const {
    sort: _sort,
    order: _order,
    preview: _preview,
    limit: _limit,
    ...query
  } = toTraceListQuery(search, range, environment);

  return useQuery({
    queryKey: queryKeys.project(projectId).traceHistogram(query),
    queryFn: async () => {
      const histogram = await projectsApi.traceHistogram(projectId, query);
      const bars = histogram.buckets.length > 0 ? fillBuckets(histogram, query) : [];
      return { bucket: histogram.bucket, bars, window: { from: query.from, to: query.to } };
    },
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

/**
 * One span with its payloads: trace detail leaves them out (`payload_omitted`) once a trace's
 * payloads pass its budget. Runs only for such a span.
 */
export function useSpanQuery(traceId: string, span: Span) {
  const { projectId } = useProjectParams();
  return useQuery(spanQuery(projectId, traceId, span));
}

function spanQuery(projectId: string, traceId: string, span: Span) {
  return {
    queryKey: queryKeys.project(projectId).span(traceId, span.span_id),
    queryFn: () => projectsApi.span(projectId, traceId, span.span_id),
    staleTime: 60_000,
    enabled: span.payload_omitted,
  };
}

/**
 * One span with its payloads by id, for a compare step's diff: the caller mounts it only once the
 * diff is open. Idle (no request) without an id, for a step missing from that trace.
 */
export function useStepSpanQuery(traceId: string, spanId: string | undefined) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).span(traceId, spanId ?? ""),
    queryFn: () => projectsApi.span(projectId, traceId, spanId ?? ""),
    staleTime: 60_000,
    enabled: spanId !== undefined,
  });
}

/**
 * `spans` with their payloads: each `payload_omitted` span is fetched alone (`useSpanQuery`'s
 * request), the others are used as given. `isPending` until every omitted span has loaded;
 * `error` is the first failure.
 */
export function useSpansWithPayloads(traceId: string, spans: readonly Span[]) {
  const { projectId } = useProjectParams();
  return useQueries({
    queries: spans.map((span) => spanQuery(projectId, traceId, span)),
    combine: (results) => ({
      spans: spans.map(
        (span, index) => (span.payload_omitted ? results[index]?.data : span) ?? span,
      ),
      isPending: spans.some((span, index) => span.payload_omitted && results[index]?.isPending),
      error: results.find((result) => result.isError)?.error ?? null,
      refetch: () => {
        for (const result of results) {
          if (result.isError) {
            void result.refetch();
          }
        }
      },
    }),
  });
}

const PREVIOUS_RUNS_DAYS = 30;

/**
 * "Compare with…": up to 6 traces of this name that started by `before` (this trace's start),
 * within the 30 days before it, newest first. The list may include this trace; callers drop it.
 * Idle for an unnamed trace.
 */
export function usePreviousRunsQuery(name: string | null, before: string) {
  const { projectId } = useProjectParams();
  const from = new Date(Date.parse(before) - PREVIOUS_RUNS_DAYS * 86_400_000).toISOString();
  const query = { from, to: before, name: name ?? undefined, limit: 6 };
  return useQuery({
    queryKey: queryKeys.project(projectId).traces(query),
    queryFn: () => projectsApi.traces(projectId, query),
    staleTime: 60_000,
    enabled: name !== null,
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

/** Findings that still need a person; resolved and muted ones are not a reason to look. */
const ACTIVE_INSIGHT_STATUSES = ["open", "acknowledged"] as const;
/** The API cites at most 20 traces per insight; a trace rarely carries more than a few. */
const TRACE_INSIGHT_LIMIT = 10;

/**
 * The trace's open Doctor findings; empty while loading and on error (a badge or flag is a hint).
 * Shared by the insight badges and the waterfall's Doctor flag: one query key, so one request.
 */
export function useTraceInsights(traceId: string): Insight[] {
  const query: InsightQuery = {
    trace_id: traceId,
    status: ACTIVE_INSIGHT_STATUSES,
    limit: TRACE_INSIGHT_LIMIT,
  };
  return useProjectInsights(query, { retry: false }).data?.items ?? [];
}
