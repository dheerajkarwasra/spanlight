import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { refreshMe } from "@/features/auth";
import { orgsApi, queryKeys, type Me, type Org, type Project } from "@/lib/api";
import type { OrgDeleteProof } from "@/lib/api/orgs";
import { clearLastProject } from "@/lib/last-project";

import { withoutOrg } from "./organization-flow";

/**
 * Deletes the organization with the typed slug, then leaves it. Only the `DELETE` can fail the
 * caller (a wrong slug, refused proof or no permission): once it answered, the organization is
 * gone, so everything after it is best effort and never reports an error.
 *
 * Leaving: `me` is read again (the organization is gone from it) and "/" picks where to go next,
 * the last project if it is still theirs, otherwise the first project of another organization,
 * otherwise onboarding. If `me` can't be read, the organization is dropped from the cached one so
 * "/" still can't route back into it, and if the move itself fails the page is reloaded at "/".
 */
export function useDeleteOrganization(
  org: Org,
): (confirm: string, proof?: OrgDeleteProof) => Promise<void> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return async (confirm, proof) => {
    // The projects are about to be gone; their ids come from the list the page already loaded.
    const projects = queryClient.getQueryData<Project[]>(queryKeys.org(org.id).projects) ?? [];

    await orgsApi.delete(org.id, confirm, proof);

    clearLastProject({ orgId: org.id });
    try {
      await refreshMe(queryClient);
    } catch {
      queryClient.setQueryData<Me | null>(queryKeys.me, (me) => (me ? withoutOrg(me, org.id) : me));
    }
    try {
      await navigate({ to: "/" });
    } catch {
      window.location.assign("/");
    }

    // After the move, so nothing on the page behind it asks for what no longer exists.
    queryClient.removeQueries({ queryKey: queryKeys.org(org.id).all });
    for (const project of projects) {
      queryClient.removeQueries({ queryKey: queryKeys.project(project.id).all });
    }
  };
}
