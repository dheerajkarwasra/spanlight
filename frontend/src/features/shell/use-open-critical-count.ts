import { useQuery } from "@tanstack/react-query";

import { insightsApi, queryKeys } from "@/lib/api";

import { useProjectParams } from "./project-context";

/** Refresh about as often as the detectors run. */
const REFRESH_MS = 60_000;

/** Critical insights of the project that are still open or acknowledged; `undefined` until known or when the call fails. */
export function useOpenCriticalCount(): number | undefined {
  const { projectId } = useProjectParams();
  const query = useQuery({
    queryKey: queryKeys.project(projectId).insightsSummary,
    queryFn: () => insightsApi.summary(projectId),
    refetchInterval: REFRESH_MS,
    // A badge is a hint: a failed fetch shows nothing rather than an error in the rail.
    retry: false,
  });
  return query.data?.open_critical;
}
