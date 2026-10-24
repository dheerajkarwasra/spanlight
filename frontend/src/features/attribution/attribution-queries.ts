import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell/project-context";
import {
  attributionApi,
  queryKeys,
  type AttributionQuery,
  type AttributionSeriesQuery,
} from "@/lib/api";

/**
 * One dimension's groups over the window, page by page. Previous rows stay while the dimension or
 * sort changes. `enabled: false` holds the fetch, e.g. for a key the API would refuse.
 */
export function useAttributionQuery(query: Omit<AttributionQuery, "cursor">, enabled = true) {
  const { projectId } = useProjectParams();

  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).attribution(query),
    queryFn: ({ pageParam }) => attributionApi.read(projectId, { ...query, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** The Spend chart's buckets: the window total, or one group's with `value`. */
export function useAttributionSeriesQuery(query: AttributionSeriesQuery) {
  const { projectId } = useProjectParams();

  return useQuery({
    queryKey: queryKeys.project(projectId).attributionSeries(query),
    queryFn: () => attributionApi.series(projectId, query),
    placeholderData: keepPreviousData,
  });
}
