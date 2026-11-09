import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import { errorMessage, orgsApi, queryKeys } from "@/lib/api";

import type { InviteInput, MemberRemoveInput, MemberRoleInput } from "@/lib/api/orgs";

export function useMembersQuery() {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).members,
    queryFn: () => orgsApi.members(orgId),
  });
}

/** Pending invites are only visible to people who can manage members. */
export function useInvitesQuery(enabled: boolean) {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).invites,
    queryFn: () => orgsApi.invites(orgId),
    enabled,
  });
}

export function useUpdateMemberRole() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    // The variables carry the password: keep nothing once the dialog is gone.
    gcTime: 0,
    mutationFn: ({ userId, ...input }: MemberRoleInput & { userId: string }) =>
      orgsApi.updateMemberRole(orgId, userId, input),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).members });
    },
  });
}

export function useRemoveMember() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    // The variables carry the password: keep nothing once the dialog is gone.
    gcTime: 0,
    mutationFn: ({ userId, ...proof }: MemberRemoveInput & { userId: string }) =>
      orgsApi.removeMember(orgId, userId, proof),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).members });
    },
  });
}

/** The response carries the one-time invite URL; it is held in component state only. */
export function useCreateInvite() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: InviteInput) => orgsApi.createInvite(orgId, input),
    gcTime: 0,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).invites });
    },
  });
}

export function useRevokeInvite() {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (inviteId: string) => orgsApi.revokeInvite(orgId, inviteId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).invites });
    },
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}
