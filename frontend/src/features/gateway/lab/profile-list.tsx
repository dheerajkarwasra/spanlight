import { FlaskConical } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { FaultProfile } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useFaultProfilesQuery } from "../gateway-queries";
import { ProfileDialog } from "./profile-dialog";
import { PROFILE_GRID, ProfileRow } from "./profile-row";

interface ProfileListProps {
  canWrite: boolean;
}

/** Figma "Fault profiles" card: loading, error, empty and the list, each in the same card. */
export function ProfileList({ canWrite }: ProfileListProps) {
  const profiles = useFaultProfilesQuery();

  return (
    <Card role="region" aria-labelledby="lab-profiles-title">
      <CardHeader>
        <div>
          <CardTitle id="lab-profiles-title">Fault profiles</CardTitle>
          <CardDescription>
            A profile fires on a share of calls from the keys it is attached to.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {profiles.isPending ? (
          <TileListSkeleton label="Loading fault profiles" rows={3} />
        ) : profiles.isError ? (
          <ErrorState
            compact
            error={profiles.error}
            title="Couldn't load fault profiles"
            onRetry={() => void profiles.refetch()}
          />
        ) : profiles.data.length === 0 ? (
          <EmptyState
            icon={FlaskConical}
            title="No fault profiles"
            description="Create a profile, then attach it to a staging or development key."
            action={canWrite ? <NewProfileButton /> : undefined}
          />
        ) : (
          <Profiles profiles={profiles.data} canWrite={canWrite} />
        )}
      </CardContent>
    </Card>
  );
}

function Profiles({ profiles, canWrite }: { profiles: FaultProfile[]; canWrite: boolean }) {
  return (
    <div className="@container flex flex-col gap-3">
      <ColumnLabels className={cn("gap-3 pr-3 pl-4 @[52rem]:grid", PROFILE_GRID)}>
        <span>Name</span>
        <span>Scenario</span>
        <span>Probability</span>
        <span>Keys</span>
        <span>Expires</span>
        <span>Status</span>
        <span>Enabled</span>
        <span />
      </ColumnLabels>
      <TileList label="Fault profiles">
        {profiles.map((profile) => (
          <ProfileRow key={profile.id} profile={profile} canWrite={canWrite} />
        ))}
      </TileList>
    </div>
  );
}

/** The empty state's own "New fault profile": the page header has the same button. */
function NewProfileButton() {
  return <ProfileDialog trigger={<Button variant="primary">New fault profile</Button>} />;
}
