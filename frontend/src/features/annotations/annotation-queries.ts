import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import {
  annotationsApi,
  orgsApi,
  queryKeys,
  type AnnotationQueue,
  type QueueCreate,
  type QueueItemStatus,
  type ReviewReason,
} from "@/lib/api";

export { useScoreConfigsQuery } from "@/features/scores";

const PAGE_SIZE = 50;

/** The project's review queues, newest first, each with its progress. */
export function useQueuesQuery() {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).annotations.list,
    queryFn: ({ pageParam }) =>
      annotationsApi.list(projectId, { cursor: pageParam, limit: PAGE_SIZE }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

export function useQueueQuery(queueId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).annotations.detail(queueId),
    queryFn: () => annotationsApi.get(projectId, queueId),
  });
}

/** One queue's items, oldest first; `status` keeps one status and `reason` one reason. */
export function useQueueItemsQuery(
  queueId: string,
  status: QueueItemStatus | undefined,
  reason: ReviewReason | undefined,
) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).annotations.items(queueId, { status, reason }),
    queryFn: ({ pageParam }) =>
      annotationsApi.items(projectId, queueId, {
        status,
        reason,
        cursor: pageParam,
        limit: PAGE_SIZE,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

/** The org's members, to name reviewers. Same cache entry as the Members page. */
export function useMembersQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).members,
    queryFn: () => orgsApi.members(orgId),
    staleTime: 5 * 60_000,
  });
}

/** Refreshes every queue view: the list, details, progress and item tables. */
export function useInvalidateQueues() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).annotations.all }),
    [projectId, queryClient],
  );
}

export function useCreateQueue() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateQueues();
  return useMutation({
    mutationFn: (input: QueueCreate) => annotationsApi.create(projectId, input),
    onSuccess: invalidate,
  });
}

/**
 * Deletes a queue and leaves its page first: dropping the cache of a page that is still mounted
 * would refetch it into a 404.
 */
export function useDeleteQueue() {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (queue: AnnotationQueue) => annotationsApi.delete(projectId, queue.id),
    onSuccess: async (_result, queue) => {
      toast.success(`Deleted queue “${queue.name}”.`);
      await navigate({ to: "/$orgId/$projectId/queues", params: { orgId, projectId } });
      const keys = queryKeys.project(projectId).annotations;
      queryClient.removeQueries({ queryKey: keys.detail(queue.id) });
      await queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

export function useAddItems(queueId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateQueues();
  return useMutation({
    mutationFn: (traceIds: string[]) => annotationsApi.addItems(projectId, queueId, traceIds),
    onSuccess: invalidate,
  });
}
