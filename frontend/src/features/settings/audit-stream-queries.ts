import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { orgsApi, queryKeys, type AuditStreamSaved } from "@/lib/api";

/** The org's audit hash chain, recomputed by the server from the stored events. */
export function useAuditIntegrityQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).auditIntegrity,
    queryFn: () => orgsApi.auditIntegrity(orgId),
  });
}

/** The org's audit stream, or null when none is set up. Polled, so delivery state stays fresh. */
export function useAuditStreamQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).auditStream,
    queryFn: () => orgsApi.auditStream(orgId),
    refetchInterval: 30_000,
  });
}

/**
 * Save, rotate, remove and test. Every write is audited, so the log and its chain are refreshed
 * along with the stream.
 */
export function useAuditStreamMutations() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  const keys = queryKeys.org(orgId);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.auditStream }),
      queryClient.invalidateQueries({ queryKey: keys.audit }),
    ]);
  };
  const saved = (result: AuditStreamSaved) => {
    queryClient.setQueryData(keys.auditStream, result.stream);
    void refresh();
  };

  return {
    save: useMutation({
      mutationFn: (body: { url: string; enabled: boolean }) => orgsApi.saveAuditStream(orgId, body),
      onSuccess: saved,
    }),
    rotate: useMutation({
      mutationFn: () => orgsApi.rotateAuditStreamSecret(orgId),
      onSuccess: saved,
    }),
    remove: useMutation({
      mutationFn: () => orgsApi.deleteAuditStream(orgId),
      onSuccess: () => {
        queryClient.setQueryData(keys.auditStream, null);
        void refresh();
      },
    }),
    test: useMutation({
      mutationFn: () => orgsApi.testAuditStream(orgId),
      onSettled: () => void refresh(),
    }),
  };
}
