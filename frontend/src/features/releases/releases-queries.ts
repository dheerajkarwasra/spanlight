import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell";
import { queryKeys, releasesApi, type ReleaseListQuery } from "@/lib/api";

/** Releases seen in the page's window and environment, the picker's options. */
export function useReleaseListQuery() {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query: ReleaseListQuery = { from: range.from, to: range.to, environment };
  return useQuery({
    queryKey: queryKeys.project(projectId).releases(query),
    queryFn: () => releasesApi.list(projectId, query),
    placeholderData: keepPreviousData,
  });
}

/**
 * `b` against `a` in the same window. Waits for two different releases and for `enabled`: the page
 * passes false while the release list is empty, failed or its window is too large.
 */
export function useReleaseCompareQuery(
  a: string | undefined,
  b: string | undefined,
  enabled: boolean,
) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const ready = enabled && a !== undefined && b !== undefined && a !== b;
  const query = {
    a: a ?? "",
    b: b ?? "",
    from: range.from,
    to: range.to,
    environment,
  };
  return useQuery({
    queryKey: queryKeys.project(projectId).releaseCompare(query),
    queryFn: () => releasesApi.compare(projectId, query),
    enabled: ready,
  });
}

/**
 * Each failure category's rate per release in the page's window and environment. A 404 means the
 * project has no `failure_category` config yet; the card then shows the set-up step.
 */
export function useFailureCategoriesQuery(enabled: boolean) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const query: ReleaseListQuery = { from: range.from, to: range.to, environment };
  return useQuery({
    queryKey: queryKeys.project(projectId).releaseFailureCategories(query),
    queryFn: () => releasesApi.failureCategories(projectId, query),
    placeholderData: keepPreviousData,
    enabled,
  });
}
