import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import {
  insightsApi,
  isApiError,
  providersApi,
  queryKeys,
  type FeedbackInput,
  type InsightDetail,
  type InsightFeedback,
  type MuteInput,
} from "@/lib/api";

import type { InsightQuery } from "./insight-filters";

/*
 * Reads and writes for the Doctor list and the insight detail page. The detectors run every
 * 15 minutes, so a minute of polling is plenty.
 */

const REFRESH_MS = 60_000;

/**
 * One page of insights for a query (no cursor): trace badges, summaries. `retry: false` suits
 * callers for which the list is only a hint.
 */
export function useProjectInsights(query: InsightQuery, options: { retry?: boolean } = {}) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).insights(query),
    queryFn: () => insightsApi.list(projectId, query),
    refetchInterval: REFRESH_MS,
    ...options,
  });
}

/** The Doctor list: pages of insights, most recently seen first ("Load more"). */
export function useInsightPages(query: InsightQuery) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    // Distinct from `useProjectInsights`' key: the cached data has a different shape.
    queryKey: [...queryKeys.project(projectId).insights(query), "pages"] as const,
    queryFn: ({ pageParam }) => insightsApi.list(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    refetchInterval: REFRESH_MS,
  });
}

/** The newest detector run: when the Doctor last checked. `undefined` data means no run yet. */
export function useLastDetectorRun() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).detectorRuns,
    queryFn: () => insightsApi.detectorRuns(projectId, 1),
    select: (runs) => runs[0] ?? null,
    refetchInterval: REFRESH_MS,
  });
}

/** Whether the project has any insight at all, in any status (one row is enough to know). */
export function useHasAnyInsight(enabled: boolean) {
  const { projectId } = useProjectParams();
  const query = { limit: 1 } as const;
  return useQuery({
    queryKey: queryKeys.project(projectId).insights(query),
    queryFn: () => insightsApi.list(projectId, query),
    select: (page) => page.items.length > 0,
    enabled,
  });
}

export function useInsightQuery(insightId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).insight(insightId),
    queryFn: () => insightsApi.get(projectId, insightId),
    // A page that never loaded (removed insight) is not polled; "Try again" refetches.
    refetchInterval: (query) => (query.state.data === undefined ? false : REFRESH_MS),
  });
}

/** Every Doctor query of the project (list, detail, summary, health) changes with an action. */
function useInvalidateDoctor() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).doctor });
}

/**
 * The caller reports errors: inline next to the buttons. Every mutation refetches the Doctor
 * queries even when it fails, so a 409 INVALID_TRANSITION leaves the page on the latest status.
 */
export function useAcknowledgeInsight(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  return useMutation({
    mutationFn: () => insightsApi.acknowledge(projectId, insightId),
    onSettled: invalidate,
  });
}

export function useResolveInsight(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  return useMutation({
    mutationFn: () => insightsApi.resolve(projectId, insightId),
    onSettled: invalidate,
  });
}

export function useUnmuteInsight(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  return useMutation({
    mutationFn: () => insightsApi.unmute(projectId, insightId),
    onSettled: invalidate,
  });
}

export function useMuteInsight(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  return useMutation({
    mutationFn: (input: MuteInput) => insightsApi.mute(projectId, insightId, input),
    onSettled: invalidate,
  });
}

/** Spends the org's monthly explain budget; a stored explanation then shows on the detail page. */
export function useExplainInsight(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  return useMutation({
    mutationFn: () => insightsApi.explain(projectId, insightId),
    onSettled: invalidate,
  });
}

/** Precision per detector kind from members' answers, all time. */
export function usePrecision() {
  const { projectId } = useProjectParams();
  const query = {} as const;
  return useQuery({
    queryKey: queryKeys.project(projectId).precision(query),
    queryFn: () => insightsApi.precision(projectId, query),
  });
}

/**
 * Whether this server polls the providers' status pages (`limit=1`: only `polling` is read).
 * Asked only for a `provider_incident` finding without a matched incident, to say why none was.
 */
export function useIncidentPolling(enabled: boolean) {
  const { projectId } = useProjectParams();
  const query = { limit: 1 } as const;
  return useQuery({
    queryKey: queryKeys.project(projectId).providerIncidents(query),
    queryFn: () => providersApi.incidents(projectId, query),
    select: (list) => list.polling,
    enabled,
  });
}

/** Writes the caller's own vote into the cached insight, so the row answers before the refetch. */
function useSetMine(insightId: string) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return (mine: InsightFeedback["mine"]) => {
    queryClient.setQueryData<InsightDetail>(
      queryKeys.project(projectId).insight(insightId),
      (insight) =>
        insight === undefined ? insight : { ...insight, feedback: { ...insight.feedback, mine } },
    );
  };
}

/** Records or replaces the caller's vote; the counts and the Precision tab refetch after. */
export function usePutFeedback(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  const setMine = useSetMine(insightId);
  return useMutation({
    mutationFn: (input: FeedbackInput) => insightsApi.putFeedback(projectId, insightId, input),
    onSuccess: (saved) => {
      setMine({ verdict: saved.verdict, note: saved.note, updated_at: saved.updated_at });
    },
    onSettled: invalidate,
  });
}

export function useDeleteFeedback(insightId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDoctor();
  const setMine = useSetMine(insightId);
  return useMutation({
    // A 404 means there is no vote to remove any more (another tab took it back): done.
    mutationFn: async () => {
      try {
        await insightsApi.deleteFeedback(projectId, insightId);
      } catch (error) {
        if (!(isApiError(error) && error.status === 404)) {
          throw error;
        }
      }
    },
    onSuccess: () => {
      setMine(null);
    },
    onSettled: invalidate,
  });
}
