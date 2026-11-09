import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { orgsApi, projectsApi, queryKeys, type Project } from "@/lib/api";
import type { ProjectDeleteProof } from "@/lib/api/projects";
import { clearLastProject } from "@/lib/last-project";

/** The organization's projects as the server has them now, or null when they can't be read. */
async function remainingProjects(
  queryClient: QueryClient,
  orgId: string,
): Promise<Project[] | null> {
  try {
    return await queryClient.query({
      queryKey: queryKeys.org(orgId).projects,
      queryFn: () => orgsApi.projects(orgId),
      staleTime: 0,
    });
  } catch {
    return null;
  }
}

/**
 * Deletes the project with the typed slug, then leaves it: to another project of the same
 * organization, or to onboarding (with this organization chosen) when it was the last one. If the
 * list can't be read, "/" decides. The remembered last project is forgotten when it was this one.
 * A rejected delete (a wrong slug, refused proof or no permission) throws before anything is changed here.
 */
export function useDeleteProject(
  project: Project,
): (confirm: string, proof?: ProjectDeleteProof) => Promise<void> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return async (confirm, proof) => {
    await projectsApi.delete(project.id, confirm, proof);
    clearLastProject({ projectId: project.id });
    // At once, so "/" and the project switcher never offer it again, even if the re-read below fails.
    queryClient.setQueryData<Project[]>(queryKeys.org(project.org_id).projects, (list) =>
      list?.filter((candidate) => candidate.id !== project.id),
    );

    const projects = await remainingProjects(queryClient, project.org_id);
    const next = projects?.find((candidate) => candidate.id !== project.id);
    if (next) {
      await navigate({
        to: "/$orgId/$projectId/overview",
        params: { orgId: project.org_id, projectId: next.id },
      });
    } else if (projects) {
      await navigate({ to: "/onboarding", search: { org: project.org_id } });
    } else {
      await navigate({ to: "/" });
    }

    // After the move, so nothing on the page behind it asks for what no longer exists.
    queryClient.removeQueries({ queryKey: queryKeys.project(project.id).all });
  };
}
