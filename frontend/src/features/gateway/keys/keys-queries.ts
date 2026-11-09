import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import {
  errorMessage,
  gatewayApi,
  queryKeys,
  type GatewayKeyCreate,
  type GatewayKeyUpdate,
} from "@/lib/api";
import { useUncachedAction } from "@/lib/use-uncached-action";

/*
 * The key list itself is `useGatewayKeysQuery` from `../gateway-queries`: the Lab page reads it
 * too. Everything that changes a key lives here.
 */

/** A key change can move a fault profile's attached keys, and the overview's per-key rows. */
function useInvalidateGateway() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).gateway.all });
}

/**
 * Creating a key returns its secret once. It goes through `useUncachedAction`, not a mutation,
 * because a mutation keeps its result in the query client's cache; the dialog keeps the result in
 * its own state and drops it when it closes.
 */
export function useCreateGatewayKey() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateGateway();
  return useUncachedAction(async (input: GatewayKeyCreate) => {
    const created = await gatewayApi.createKey(projectId, input);
    void invalidate();
    return created;
  });
}

interface UpdateKeyVariables {
  keyId: string;
  update: GatewayKeyUpdate;
}

/** The caller reads the error: a field problem goes on the field, anything else is a toast. */
export function useUpdateGatewayKey() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateGateway();
  return useMutation({
    gcTime: 0,
    mutationFn: ({ keyId, update }: UpdateKeyVariables) =>
      gatewayApi.updateKey(projectId, keyId, update),
    onSuccess: invalidate,
  });
}

export function useRevokeGatewayKey() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateGateway();
  return useMutation({
    mutationFn: (keyId: string) => gatewayApi.revokeKey(projectId, keyId),
    onSuccess: invalidate,
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}

export function usePurgeGatewayCache() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateGateway();
  return useMutation({
    mutationFn: () => gatewayApi.purgeCache(projectId),
    onSuccess: invalidate,
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}
