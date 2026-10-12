import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { useProjectParams } from "@/features/shell";
import {
  annotationsApi,
  orgsApi,
  queryKeys,
  type QueueCreate,
  type QueueItemStatus,
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

/** One queue's items, oldest first; `status` keeps one status. */
export function useQueueItemsQuery(queueId: string, status: QueueItemStatus | undefined) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).annotations.items(queueId, { status }),
    queryFn: ({ pageParam }) =>
      annotationsApi.items(projectId, queueId, { status, cursor: pageParam, limit: PAGE_SIZE }),
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

export function useAddItems(queueId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateQueues();
  return useMutation({
    mutationFn: (traceIds: string[]) => annotationsApi.addItems(projectId, queueId, traceIds),
    onSuccess: invalidate,
  });
}
