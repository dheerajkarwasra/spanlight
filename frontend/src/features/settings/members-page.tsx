import { Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { useMe } from "@/features/auth";
import { useCurrentOrg, usePermission } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";

import { InviteSection } from "./invite-section";
import { MemberList } from "./member-list";
import { useMembersQuery } from "./member-queries";
import { sortMembers } from "./member-utils";
import { PendingInvitesSection } from "./pending-invites-section";

export function MembersPage() {
  const canManage = usePermission("member:manage");

  return (
    <div className="flex flex-col gap-5">
      {canManage ? null : (
        <ReadOnlyNote>{viewOnlyCopy("members and invitations", "member:manage")}</ReadOnlyNote>
      )}
      <MembersSection canManage={canManage} />
      {canManage ? (
        <>
          <InviteSection />
          <PendingInvitesSection />
        </>
      ) : null}
    </div>
  );
}

function MembersSection({ canManage }: { canManage: boolean }) {
  const me = useMe();
  const org = useCurrentOrg();
  const membersQuery = useMembersQuery();
  const orgName = org?.name ?? "this organization";
  const count = membersQuery.data?.length;

  return (
    <SectionCard
      title="Members"
      description={`People in ${orgName}. Roles apply to every project in the organization.`}
      actions={
        count === undefined ? null : (
          <Badge className="tabular">
            {count} {count === 1 ? "member" : "members"}
          </Badge>
        )
      }
      className="gap-3"
    >
      {membersQuery.isPending ? (
        <TileListSkeleton label="Loading members" rows={3} className="h-[59px]" />
      ) : null}
      {membersQuery.isError ? (
        <ErrorState
          compact
          error={membersQuery.error}
          title="Couldn't load members"
          onRetry={() => {
            void membersQuery.refetch();
          }}
        />
      ) : null}
      {membersQuery.isSuccess && membersQuery.data.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No members"
          description="This organization has no members you can see."
        />
      ) : null}
      {membersQuery.isSuccess && membersQuery.data.length > 0 ? (
        <MemberList
          members={sortMembers(membersQuery.data)}
          currentUserId={me.user.id}
          canManage={canManage}
          orgName={orgName}
        />
      ) : null}
    </SectionCard>
  );
}
