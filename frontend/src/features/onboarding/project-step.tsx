import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Folder, Lock } from "lucide-react";
import { toast } from "sonner";

import { Notice } from "@/components/notice";
import { orgsApi, queryKeys, type Membership, type Project } from "@/lib/api";
import { can } from "@/lib/permissions";

import { ONBOARDING_COPY } from "./copy";
import { NameForm } from "./name-form";
import { StepCard } from "./step-card";

const onboardingRoute = getRouteApi("/_authed/onboarding");

interface ProjectStepProps {
  membership: Membership;
}

export function ProjectStep({ membership }: ProjectStepProps) {
  const navigate = onboardingRoute.useNavigate();
  const queryClient = useQueryClient();
  const orgId = membership.org.id;

  async function createProject(name: string) {
    const project = await orgsApi.createProject(orgId, name);
    // Seed the cache so step 3 can verify the project without waiting for a refetch.
    queryClient.setQueryData<Project[]>(queryKeys.org(orgId).projects, (projects) =>
      projects ? [...projects, project] : [project],
    );
    void queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).projects });
    toast.success(`Created ${project.name}`);
    await navigate({ search: { org: orgId, project: project.id } });
  }

  return (
    <StepCard
      icon={Folder}
      title={ONBOARDING_COPY.project.cardTitle}
      description={ONBOARDING_COPY.project.cardDescription}
    >
      {can(membership.role, "project:write") ? (
        <NameForm
          noun="project"
          label="Project name"
          defaultValue="My app"
          submitLabel="Create project"
          onCreate={createProject}
        />
      ) : (
        <Notice icon={Lock}>
          Only owners and admins can create projects in {membership.org.name}. Ask one of them to
          create a project, or create your own organization.
        </Notice>
      )}
    </StepCard>
  );
}
