import { useQuery } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell/project-context";
import { costExplanationApi, queryKeys } from "@/lib/api";

/**
 * One span's cost explanation, fetched only once the card is expanded (`enabled`). A stored span
 * is explained from what was stored, so the answer rarely changes.
 */
export function useCostExplanationQuery(
  traceId: string,
  spanId: string,
  options: { enabled: boolean },
) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).costExplanation(traceId, spanId),
    queryFn: () => costExplanationApi.get(projectId, traceId, spanId),
    staleTime: 60_000,
    enabled: options.enabled,
  });
}
