import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { useProjectParams } from "@/features/shell";
import {
  queryKeys,
  scoresApi,
  type ScoreConfig,
  type ScoreConfigCreate,
  type ScoreConfigUpdate,
  type ScoreCreate,
  type ScoreDataType,
} from "@/lib/api";

/**
 * A random (version 4) UUID for a new score, so a retried request stores it once. Built from
 * `getRandomValues`, which, unlike `randomUUID`, also works on a dashboard served over plain HTTP.
 */
export function newScoreId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** The project's score configs, by name. Small and rarely changed, so shared by every screen. */
export function useScoreConfigsQuery(enabled = true) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).scoreConfigs,
    queryFn: () => scoresApi.configs(projectId),
    staleTime: 60_000,
    enabled,
  });
}

/**
 * Looks up a score name's type in the configs: undefined while they load, null for a name without
 * a config (or when they could not be loaded).
 */
export function useScoreTypeOf(): (name: string) => ScoreDataType | null | undefined {
  const query = useScoreConfigsQuery();
  return (name) => {
    if (query.isPending) {
      return undefined;
    }
    return query.data?.find((config) => config.name === name)?.data_type ?? null;
  };
}

/** Every score of one trace, newest first. */
export function useTraceScoresQuery(traceId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).traceScores(traceId),
    queryFn: () => scoresApi.forTrace(projectId, traceId),
  });
}

/** A new or removed score changes the trace's panel, its list row and the score-name options. */
function refreshAfterScoreChange(queryClient: QueryClient, projectId: string, traceId: string) {
  const keys = queryKeys.project(projectId);
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.traceScores(traceId) }),
    queryClient.invalidateQueries({ queryKey: [...keys.all, "traces"] }),
    queryClient.invalidateQueries({ queryKey: keys.filters }),
    // A first score of a new name creates its config.
    queryClient.invalidateQueries({ queryKey: keys.scoreConfigs }),
  ]);
}

/**
 * Refreshes every view of a trace's scores after scores were written elsewhere, e.g. when a
 * review queue item is completed: `refresh(traceId)`.
 */
export function useRefreshScores() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useCallback(
    (traceId: string) => refreshAfterScoreChange(queryClient, projectId, traceId),
    [queryClient, projectId],
  );
}

/** Adds a human score to the trace; the caller is its author. */
export function useCreateScore(traceId: string) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ScoreCreate) => scoresApi.create(projectId, traceId, input),
    onSuccess: () => refreshAfterScoreChange(queryClient, projectId, traceId),
  });
}

export function useDeleteScore(traceId: string) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (scoreId: string) => scoresApi.delete(projectId, scoreId),
    onSuccess: () => refreshAfterScoreChange(queryClient, projectId, traceId),
  });
}

function useConfigsRefresh() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).scoreConfigs });
}

export function useCreateScoreConfig() {
  const { projectId } = useProjectParams();
  const refresh = useConfigsRefresh();
  return useMutation({
    mutationFn: (input: ScoreConfigCreate): Promise<ScoreConfig> =>
      scoresApi.createConfig(projectId, input),
    onSuccess: refresh,
  });
}

export function useUpdateScoreConfig() {
  const { projectId } = useProjectParams();
  const refresh = useConfigsRefresh();
  return useMutation({
    mutationFn: ({ id, update }: { id: string; update: ScoreConfigUpdate }) =>
      scoresApi.updateConfig(projectId, id, update),
    onSuccess: refresh,
  });
}

export function useDeleteScoreConfig() {
  const { projectId } = useProjectParams();
  const refresh = useConfigsRefresh();
  return useMutation({
    mutationFn: (configId: string) => scoresApi.deleteConfig(projectId, configId),
    onSuccess: refresh,
  });
}

/**
 * Saves the failure categories: creates the `failure_category` config, or replaces the list of
 * the existing one. Refreshes the configs and the Releases card that counts them.
 */
export function useSaveFailureCategories(config: ScoreConfig | undefined) {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const keys = queryKeys.project(projectId);
  return useMutation({
    mutationFn: (categories: string[]): Promise<ScoreConfig> =>
      config
        ? scoresApi.updateConfig(projectId, config.id, { categories })
        : scoresApi.createFailureCategory(projectId, categories),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.scoreConfigs }),
        queryClient.invalidateQueries({
          queryKey: [...keys.all, "releases", "failure-categories"],
        }),
      ]),
  });
}
