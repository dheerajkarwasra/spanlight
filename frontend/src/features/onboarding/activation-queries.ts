import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useMe } from "@/features/auth";
import { useCurrentOrg, useHasTraces, useProjectParams } from "@/features/shell";
import { authApi, errorMessage, projectsApi, queryKeys, type Me } from "@/lib/api";

import { checklistVisible } from "./activation-steps";

/** Everything ruling 12 needs except the steps themselves, which are read only when they matter. */
export function useChecklistGate() {
  const me = useMe();
  const org = useCurrentOrg();
  return {
    hasTraces: useHasTraces(),
    isDemoOrg: org?.is_demo ?? false,
    dismissed: me.activation_checklist_dismissed,
  };
}

/** The project's four setup steps; the client default `staleTime` (30 s) applies. */
export function useActivationQuery(enabled: boolean) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).activation,
    queryFn: () => projectsApi.activation(projectId),
    enabled,
  });
}

/**
 * Hides or restores the checklist for the signed-in user (every project at once). The cached `/me`
 * changes first so the tile goes at once, and goes back if the server refuses (the demo's 403).
 */
export function useSetActivationChecklist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dismissed: boolean) => authApi.setActivationChecklist(dismissed),
    onMutate: async (dismissed) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.me });
      const previous = queryClient.getQueryData<Me | null>(queryKeys.me);
      if (previous) {
        queryClient.setQueryData<Me>(queryKeys.me, {
          ...previous,
          activation_checklist_dismissed: dismissed,
        });
      }
      return { previous };
    },
    onError: (error, _dismissed, context) => {
      queryClient.setQueryData(queryKeys.me, context?.previous);
      toast.error(errorMessage(error));
    },
  });
}

/**
 * Whether the account menu offers "Show setup checklist": dismissed, and the checklist would show
 * again. The steps are read only while the menu is open, since a dismissed checklist reads nothing.
 */
export function useCanRestoreChecklist(menuOpen: boolean): boolean {
  const gate = useChecklistGate();
  const activation = useActivationQuery(
    menuOpen && gate.dismissed && gate.hasTraces === true && !gate.isDemoOrg,
  );
  return (
    gate.dismissed && checklistVisible({ ...gate, dismissed: false, activation: activation.data })
  );
}
