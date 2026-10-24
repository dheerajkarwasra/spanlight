import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell";
import { priceOverridesApi, pricesApi, queryKeys, type PriceOverrideCreate } from "@/lib/api";

/** The shared price table changes with a release or a price sync, rarely within a session. */
export function usePricesQuery() {
  return useQuery({
    queryKey: queryKeys.prices,
    queryFn: () => pricesApi.list(),
    staleTime: 10 * 60_000,
  });
}

/** This project's models with usage but no price, in the page's window and environment. */
export function useUnpricedModelsQuery() {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const window = { from: range.from, to: range.to };
  return useQuery({
    queryKey: queryKeys.project(projectId).unpricedModels(window, environment),
    queryFn: () => pricesApi.unpricedModels(projectId, window, environment),
    placeholderData: keepPreviousData,
  });
}

/** The organization's own prices (readable by every member, changed with `gateway:write`). */
export function usePriceOverridesQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).priceOverrides,
    queryFn: () => priceOverridesApi.list(orgId),
  });
}

/** A new override changes the org's list, and what this project's models are priced by. */
export function useCreatePriceOverride() {
  const { orgId, projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: PriceOverrideCreate) => priceOverridesApi.create(orgId, input),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).priceOverrides }),
        queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).unpricedModelsAll }),
        queryClient.invalidateQueries({ queryKey: queryKeys.prices }),
      ]),
  });
}

export function useDeletePriceOverride() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (overrideId: string) => priceOverridesApi.delete(orgId, overrideId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).priceOverrides }),
  });
}
