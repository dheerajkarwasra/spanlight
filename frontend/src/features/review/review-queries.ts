import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { useProjectParams } from "@/features/shell";
import {
  datasetsApi,
  isApiError,
  projectsApi,
  queryKeys,
  reviewApi,
  type ReviewPolicy,
  type ReviewPolicyInput,
} from "@/lib/api";

/** The project's review policy; null while it has none (the API's `404 NOT_FOUND`). */
export function useReviewPolicyQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).review.policy,
    queryFn: async (): Promise<ReviewPolicy | null> => {
      try {
        return await reviewApi.get(projectId);
      } catch (error) {
        if (isApiError(error) && error.code === "NOT_FOUND") {
          return null;
        }
        throw error;
      }
    },
  });
}

/**
 * The environments the project's traces carry, to pick from. Same cache entry as the traces
 * filters and the evaluator form.
 */
export function useEnvironmentsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).filters,
    queryFn: () => projectsApi.filters(projectId),
    staleTime: 5 * 60_000,
    select: (filters) => filters.environments,
  });
}

/**
 * Refreshes everything a policy write or run can change: the policy and its selections, the
 * queues (a run fills one) and the score configs (set-up can create one).
 */
export function useInvalidateReview() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useCallback(() => {
    const keys = queryKeys.project(projectId);
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.review.all }),
      queryClient.invalidateQueries({ queryKey: keys.annotations.all }),
      queryClient.invalidateQueries({ queryKey: keys.scoreConfigs }),
    ]);
  }, [projectId, queryClient]);
}

/** Creates or replaces the policy; send every field (see `policyInput`). */
export function useSavePolicy() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (input: ReviewPolicyInput) => reviewApi.put(projectId, input),
    onSuccess: invalidate,
  });
}

/** Deletes the policy; its queue and selection history stay. */
export function useDeletePolicy() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: () => reviewApi.delete(projectId),
    onSuccess: invalidate,
  });
}

/** Queues the selection of one completed UTC day (`YYYY-MM-DD`). */
export function useRequestRun() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (day: string) => reviewApi.requestRun(projectId, day),
    onSuccess: invalidate,
  });
}

interface SaveFromReviewInput {
  datasetId: string;
  traceId: string;
  spanId: string;
  /** The item an earlier attempt copied but could not mark; the retry only marks it. */
  copiedItemId: string | null;
  metadata: Record<string, unknown>;
  onCopied: (itemId: string) => void;
}

/**
 * Annotate's "Save as dataset item": copies the span through the from-traces route, then writes
 * `metadata.review` on the copy. Resolves to the copy's rejection reason, or null once saved.
 */
export function useSaveFromReview() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: SaveFromReviewInput): Promise<string | null> => {
      let itemId = input.copiedItemId;
      if (itemId === null) {
        const added = await datasetsApi.addItemsFromTraces(
          projectId,
          input.datasetId,
          [input.traceId],
          input.spanId,
        );
        const rejected = added.rejected[0];
        itemId = added.item_ids[0] ?? null;
        if (rejected !== undefined || itemId === null) {
          return rejected?.reason ?? "not copied";
        }
        input.onCopied(itemId);
      }
      await datasetsApi.updateItem(projectId, input.datasetId, itemId, {
        metadata: input.metadata,
      });
      return null;
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).datasets.all }),
  });
}
