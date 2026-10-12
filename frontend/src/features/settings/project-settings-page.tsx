import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ScoreConfigsCard } from "@/features/scores";
import { useCurrentOrg, usePermission, useProjectQuery } from "@/features/shell";

import { ProjectDangerZone } from "./project-danger-zone";
import { ProjectSettingsForm } from "./project-settings-form";

/**
 * General and Data settings, the project's score configs, then the "Danger zone". Deleting a project needs `project:delete`
 * (admins and owners), and the public demo's project can't be deleted, so neither sees the card.
 */
export function ProjectSettingsPage() {
  const projectQuery = useProjectQuery();
  const org = useCurrentOrg();
  const canEdit = usePermission("project:write");
  const canDelete = usePermission("project:delete") && org !== null && !org.is_demo;

  if (projectQuery.isPending) {
    return <ProjectSettingsSkeleton />;
  }

  if (projectQuery.isError) {
    return (
      <Card>
        <ErrorState
          error={projectQuery.error}
          title="Couldn't load project settings"
          onRetry={() => {
            void projectQuery.refetch();
          }}
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {canEdit ? null : (
        <ReadOnlyNote>
          Only admins and owners can change the General and Data settings.
        </ReadOnlyNote>
      )}
      {/* Keyed by project so switching projects starts a fresh form. */}
      <ProjectSettingsForm
        key={projectQuery.data.id}
        project={projectQuery.data}
        canEdit={canEdit}
      />
      <ScoreConfigsCard />
      {canDelete ? <ProjectDangerZone project={projectQuery.data} /> : null}
    </div>
  );
}

function ProjectSettingsSkeleton() {
  return (
    <div role="status" aria-label="Loading project settings" className="flex flex-col gap-5">
      <Card className="flex flex-col gap-5 p-5 sm:p-6">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-3.5 w-72 max-w-full" />
        </div>
        <div className="grid gap-5 sm:grid-cols-2">
          <Skeleton className="h-[103px] rounded-input" />
          <Skeleton className="h-[103px] rounded-input" />
        </div>
      </Card>
      <Card className="flex flex-col gap-5 p-5 sm:p-6">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-3.5 w-80 max-w-full" />
        </div>
        <Skeleton className="h-[103px] w-[220px] max-w-full rounded-input" />
        <Skeleton className="h-[78px] rounded-tile" />
      </Card>
    </div>
  );
}
