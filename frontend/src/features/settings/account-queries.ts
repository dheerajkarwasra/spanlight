import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { clearBannerDismissal } from "@/features/auth";
import { clearTraceHistory } from "@/features/traces/last-traces-search";
import {
  authApi,
  errorMessage,
  isApiError,
  orgsApi,
  queryKeys,
  type Me,
  type Org,
} from "@/lib/api";

export function useAuthSessionsQuery() {
  return useQuery({
    queryKey: queryKeys.authSessions,
    queryFn: authApi.sessions,
  });
}

export function useRevokeAuthSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => authApi.revokeSession(sessionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.authSessions });
    },
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}

/** Sign out everywhere else: ends every session except the one making the call. */
export function useRevokeOtherAuthSessions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: authApi.revokeOtherSessions,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.authSessions });
    },
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}

/**
 * Whether the error is `403 REAUTH_REQUIRED`: an account without a password whose sign-in is older
 * than 10 minutes. Signing in again is the only way through.
 */
export function isReauthRequired(error: unknown): boolean {
  return isApiError(error) && error.code === "REAUTH_REQUIRED";
}

export function useUpdateName() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => authApi.updateMe({ name }),
    onSuccess: (user) => {
      queryClient.setQueryData<Me | null>(queryKeys.me, (me) => (me ? { ...me, user } : me));
    },
  });
}

/** Turns the caller's weekly digest on or off, for every project. */
export function useSetWeeklyDigest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (weeklyDigest: boolean) => authApi.updateMe({ weekly_digest: weeklyDigest }),
    onSuccess: (user, weeklyDigest) => {
      queryClient.setQueryData<Me | null>(queryKeys.me, (me) =>
        me ? { ...me, user, weekly_digest: weeklyDigest } : me,
      );
    },
  });
}

export function useRequestEmailChange() {
  return useMutation({
    // The variables hold the password: don't keep them in the mutation cache after use.
    gcTime: 0,
    mutationFn: authApi.requestEmailChange,
  });
}

/**
 * The organizations the caller is the only owner of, read from their memberships and each owned
 * organization's members; undefined while those load. A list that fails to load counts as not
 * blocking: the server's `409 SOLE_OWNER` has the last word.
 */
export function useSoleOwnerOrgs(me: Me): Org[] | undefined {
  const owned = me.memberships
    .filter(({ org, role }) => role === "owner" && !org.is_demo)
    .map(({ org }) => org);
  return useQueries({
    queries: owned.map((org) => ({
      queryKey: queryKeys.org(org.id).members,
      queryFn: () => orgsApi.members(org.id),
    })),
    combine: (results) =>
      results.some((result) => result.isPending)
        ? undefined
        : owned.filter((_, index) => {
            const members = results[index]?.data;
            return members?.every(({ user, role }) => role !== "owner" || user.id === me.user.id);
          }),
  });
}

/**
 * Deletes the account, then leaves as signing out does: the cache and per-person browser state go,
 * and the landing page says it is done.
 */
export function useDeleteAccount() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    gcTime: 0,
    mutationFn: authApi.deleteAccount,
    onSuccess: async () => {
      queryClient.clear();
      clearTraceHistory();
      clearBannerDismissal();
      await navigate({ to: "/" });
      toast.success("Account deleted");
    },
  });
}
