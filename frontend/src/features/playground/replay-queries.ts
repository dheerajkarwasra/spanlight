import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { queryKeys, replaysApi, type ReplayInput } from "@/lib/api";

// The span panel's reads, shared so both screens hit one cache entry per span and per month.
export { useReplayBudgetQuery, useReplaySourceQuery } from "@/features/traces/replay-queries";

/**
 * Sends one replay. A key per click keeps a retried request from spending twice; afterwards the
 * budget, the span's replay list and its source read are refetched.
 */
export function useRunReplay() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ input, key }: { input: ReplayInput; key: string }) =>
      replaysApi.run(projectId, input, key),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).replays.all }),
  });
}
