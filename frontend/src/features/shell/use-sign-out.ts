import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { clearTraceHistory } from "@/features/traces/last-traces-search";
import { clearBannerDismissal } from "@/features/auth";
import { authApi, errorMessage } from "@/lib/api";

/** `next`: where to return after signing in again (the login page checks it with `safeNextPath`). */
export function useSignOut(next?: string) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: authApi.logout,
    onSuccess: async () => {
      queryClient.clear();
      clearTraceHistory();
      clearBannerDismissal();
      await navigate({ to: "/login", search: next ? { next } : {} });
    },
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}
