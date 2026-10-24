import { useQuery } from "@tanstack/react-query";

import { projectsApi, queryKeys } from "@/lib/api";

import { useProjectParams } from "./project-context";

/** Whether the project has received a trace yet; `undefined` until known or when the call fails. */
export function useHasTraces(): boolean | undefined {
  const { projectId } = useProjectParams();
  const query = useQuery({
    // The Overview's key: its first-run check and the rail's Connect tile share one answer.
    queryKey: queryKeys.project(projectId).onboarding,
    queryFn: () => projectsApi.onboarding(projectId),
  });
  return query.data?.has_traces;
}
