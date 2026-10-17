import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useFilterOptionsQuery, useProjectParams, useProjectQuery } from "@/features/shell";
import { budgetsApi, queryKeys, type BudgetInput, type BudgetUpdate } from "@/lib/api";

export { useAlertChannelsQuery } from "@/features/alerts";
export { useGatewayKeysQuery } from "@/features/gateway";

/** The project's budgets with their last evaluated state. Evaluation runs every minute. */
export function useBudgetsQuery() {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).budgets,
    queryFn: () => budgetsApi.list(projectId),
    refetchInterval: 60_000,
  });
}

export interface MetadataKeyOption {
  value: string;
  promoted: boolean;
}

/**
 * What the scope picker suggests: the project's promoted metadata keys first, then the keys and
 * the trace names seen in the last 7 days (`/filters`, the shell's cache entry). Empty while
 * loading or on an older server; the fields accept free text either way.
 */
export function useScopeSuggestions(): {
  metadataKeys: MetadataKeyOption[];
  traceNames: readonly string[];
  promotedKeys: readonly string[] | undefined;
} {
  const project = useProjectQuery();
  const filters = useFilterOptionsQuery();
  const promoted = project.data?.promoted_metadata_keys;
  const seen = (filters.data?.metadata_keys ?? []).filter((key) => !promoted?.includes(key));
  return {
    metadataKeys: [
      ...(promoted ?? []).map((value) => ({ value, promoted: true })),
      ...seen.map((value) => ({ value, promoted: false })),
    ],
    traceNames: filters.data?.trace_names ?? [],
    promotedKeys: promoted,
  };
}

function useInvalidateBudgets() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).budgets });
}

export function useCreateBudget() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: (input: BudgetInput) => budgetsApi.create(projectId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateBudget() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: ({ budgetId, update }: { budgetId: string; update: BudgetUpdate }) =>
      budgetsApi.update(projectId, budgetId, update),
    onSuccess: invalidate,
  });
}

export function useDeleteBudget() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: (budgetId: string) => budgetsApi.delete(projectId, budgetId),
    onSuccess: invalidate,
  });
}
