import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import {
  billingSourcesApi,
  queryKeys,
  type BillingSourceCreate,
  type BillingSourceUpdate,
} from "@/lib/api";
import { useUncachedAction } from "@/lib/use-uncached-action";

export function useBillingSourcesQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).billingSources,
    queryFn: () => billingSourcesApi.list(orgId),
  });
}

function useInvalidateSources() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).billingSources });
}

/**
 * The body carries the admin key, so it runs through `useUncachedAction`: a mutation would keep
 * the key in the query client's mutation cache.
 */
export function useCreateSource() {
  const { orgId } = useProjectParams();
  const invalidate = useInvalidateSources();
  return useUncachedAction(async (input: BillingSourceCreate) => {
    const created = await billingSourcesApi.create(orgId, input);
    void invalidate();
    return created;
  });
}

/** Same as creating: the new key must stay out of the mutation cache. */
export function useRotateSource() {
  const { orgId } = useProjectParams();
  const invalidate = useInvalidateSources();
  return useUncachedAction(async (sourceId: string, adminKey: string) => {
    const rotated = await billingSourcesApi.rotate(orgId, sourceId, adminKey);
    void invalidate();
    return rotated;
  });
}

/** An edit carries no key, only names, the scope, the switch and project ids. */
export function useUpdateSource() {
  const { orgId } = useProjectParams();
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: ({ sourceId, input }: { sourceId: string; input: BillingSourceUpdate }) =>
      billingSourcesApi.update(orgId, sourceId, input),
    onSettled: invalidate,
  });
}

/**
 * Deleting does not reload the list by itself: the dialog lives in the deleted source's row, so it
 * first closes and moves focus off the row, then calls `reload`.
 */
export function useDeleteSource() {
  const { orgId } = useProjectParams();
  const reload = useInvalidateSources();
  const mutation = useMutation({
    mutationFn: (sourceId: string) => billingSourcesApi.delete(orgId, sourceId),
  });
  return { ...mutation, reload };
}

/** Queues a fetch; the source's status changes once the worker has run it. */
export function useRefreshSource() {
  const { orgId } = useProjectParams();
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (sourceId: string) => billingSourcesApi.refresh(orgId, sourceId),
    onSuccess: invalidate,
  });
}

/** The file stays in component state only, not in the mutation cache. */
export function useUploadCsv() {
  const { orgId } = useProjectParams();
  const invalidate = useInvalidateSources();
  return useUncachedAction(async (sourceId: string, file: File) => {
    const result = await billingSourcesApi.upload(orgId, sourceId, file);
    void invalidate();
    return result;
  });
}
