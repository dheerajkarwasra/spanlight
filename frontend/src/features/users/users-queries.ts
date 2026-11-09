import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import { endUsersApi, queryKeys } from "@/lib/api";
import { useLiveProject } from "@/lib/live/use-live-project";

import { toUserListQuery, type UsersListState } from "./user-sort";

/**
 * End users matching `q`, ranked by `sort` and `order` over the selected range and environment.
 * Previous rows stay while the sort or search changes.
 */
export function useUserListQuery(list: UsersListState) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = toUserListQuery(list, range, environment);
  const { refetchInterval } = useLiveProject(projectId, range.value !== "custom");

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).users(query),
    queryFn: ({ pageParam, signal }) =>
      endUsersApi.list(projectId, { ...query, cursor: pageParam }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

/** One user's totals, daily rows and recent sessions over the selected range and environment. */
export function useUserDetailQuery(userId: string) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query = { from: range.from, to: range.to, environment };

  return useQuery({
    queryKey: queryKeys.project(projectId).user(userId, query),
    queryFn: () => endUsersApi.get(projectId, userId, query),
  });
}
