import { useQuery } from "@tanstack/react-query";

import { insightsApi, queryKeys } from "@/lib/api";

import { useProjectParams } from "./project-context";

/** Refresh about as often as the detectors run. */
const REFRESH_MS = 60_000;

/** Critical insights of the project that are still open or acknowledged; `undefined` until known or when the call fails. */
function useOpenCriticalCount(): number | undefined {
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

/** The Doctor's count of critical insights that still need attention; nothing while there are none. */
export function OpenCriticalBadge() {
  const count = useOpenCriticalCount();
  if (!count) {
    return null;
  }
  return (
    <span
      role="img"
      aria-label={`${count} critical ${count === 1 ? "insight needs" : "insights need"} attention`}
      className="rounded-full bg-danger px-1.5 py-px text-2xs font-bold text-danger-foreground tabular"
    >
      <span aria-hidden>{count > 99 ? "99+" : count}</span>
    </span>
  );
}
