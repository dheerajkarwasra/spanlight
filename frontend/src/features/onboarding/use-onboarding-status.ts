import { useQuery } from "@tanstack/react-query";

import { projectsApi, queryKeys, type Activation, type OnboardingStatus } from "@/lib/api";

export interface ConnectionStatus extends OnboardingStatus {
  activation?: Activation;
}

export const POLL_INTERVAL_MS = 2000;

/**
 * Polls the onboarding endpoint every 2 s until the project's first application trace arrives. Polling
 * pauses while the tab is hidden (React Query's default) and stops for good once `activation.first_trace`
 * is true. Pass null to disable it (before step 3).
 */
export function useOnboardingStatus(projectId: string | null) {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? "").connectionStatus,
    queryFn: async ({ signal }): Promise<ConnectionStatus> => {
      const [status, activation] = await Promise.all([
        projectsApi.onboarding(projectId ?? "", signal),
        projectsApi.activation(projectId ?? ""),
      ]);
      return { ...status, activation };
    },
    enabled: projectId !== null,
    // A disconnected browser must show recovery, rather than silently pause with cached data.
    networkMode: "always",
    refetchInterval: (query) =>
      query.state.data?.activation?.first_trace ? false : POLL_INTERVAL_MS,
  });
}

/**
 * Loads the project's first trace once (name, duration and cost for the success card).
 * Uses activation's identity so a setup trace cannot win a timestamp tie.
 */
export function useFirstTrace(projectId: string, traceId: string | null) {
  return useQuery({
    queryKey: queryKeys.project(projectId).trace(traceId ?? ""),
    queryFn: ({ signal }) => projectsApi.trace(projectId, traceId ?? "", signal),
    enabled: traceId !== null,
    staleTime: Infinity,
  });
}
