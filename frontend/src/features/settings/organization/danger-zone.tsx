import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useOrgProjectsQuery } from "@/features/shell";
import type { Org } from "@/lib/api";

import { ConfirmSlugDialog } from "../confirm-slug-dialog";
import { orgDeleteExplanation } from "../danger-copy";
import { DangerZoneCard } from "../danger-zone-card";
import { useMemberReauth } from "../use-member-reauth";
import { ProjectsTile } from "./projects-tile";
import { useDeleteOrganization } from "./use-delete-organization";

/**
 * The organization's "Danger zone" (Figma "Settings — Organization"): delete it with its projects,
 * members and audit log, with its typed slug and account-owner proof. The page shows it only to
 * owners of a real organization; the public demo organization never gets one.
 */
export function OrgDangerZone({ org }: { org: Org }) {
  const confirmation = useMemberReauth();
  const projectsQuery = useOrgProjectsQuery(org.id);
  const deleteOrganization = useDeleteOrganization(org);
  const explanation = orgDeleteExplanation(org.name, projectsQuery.data?.length ?? null);

  return (
    <DangerZoneCard label="Delete organization" explanation={explanation}>
      <ConfirmSlugDialog
        trigger={<Button variant="danger">Delete organization</Button>}
        slug={org.slug}
        confirmation={confirmation}
        copy={{
          title: "Delete organization",
          description: explanation,
          confirmLabel: "Delete organization",
        }}
        onConfirm={async (confirm, proof) => {
          await deleteOrganization(confirm, proof);
          toast.success(`Deleted ${org.name}.`);
        }}
      >
        <ProjectsTile projects={projectsQuery.data} failed={projectsQuery.isError} />
      </ConfirmSlugDialog>
    </DangerZoneCard>
  );
}
