import type { ReactNode } from "react";
import { toast } from "sonner";

import { RelativeTime } from "@/components/relative-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { formatTimestamp } from "@/lib/format";
import { errorMessage, type FaultProfile } from "@/lib/api";
import { cn } from "@/lib/utils";

import { profileStatus, type ProfileStatus } from "./lab-profile";
import { useToggleFaultProfile } from "./lab-queries";
import { ProfileDialog } from "./profile-dialog";
import { formatProbability } from "./scenario-params";

/** The columns of the wide layout, shared with the labels above the rows. */
export const PROFILE_GRID =
  "@[52rem]:grid-cols-[minmax(0,1.5fr)_minmax(0,1.4fr)_5rem_4.5rem_minmax(0,1.2fr)_5.5rem_3rem_4.5rem]";

const STATUS_BADGE: Record<
  ProfileStatus,
  { label: string; variant: "success" | "neutral" | "warning" }
> = {
  active: { label: "Active", variant: "success" },
  disabled: { label: "Disabled", variant: "neutral" },
  expired: { label: "Expired", variant: "warning" },
};

interface ProfileRowProps {
  profile: FaultProfile;
  canWrite: boolean;
}

/** Figma "Gateway/Fault profile row": one tile per profile, a grid from 832 px, stacked below. */
export function ProfileRow({ profile, canWrite }: ProfileRowProps) {
  const toggle = useToggleFaultProfile();
  const status = profileStatus(profile);
  const badge = STATUS_BADGE[status];
  const keyCount = profile.attached_key_ids.length;

  return (
    <li
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 rounded-tile bg-surface-muted p-3.5 @[52rem]:py-2.5 @[52rem]:pr-3 @[52rem]:pl-4",
        PROFILE_GRID,
      )}
    >
      <span
        title={profile.name}
        className="order-1 truncate text-sm font-semibold text-foreground @[52rem]:order-0"
      >
        {profile.name}
      </span>
      <code className="order-3 col-span-2 w-fit justify-self-start rounded-full border border-border bg-surface px-2 py-0.5 font-mono text-xs text-foreground @[52rem]:order-0 @[52rem]:col-span-1">
        {profile.scenario}
      </code>
      <div className="order-4 col-span-2 grid grid-cols-3 gap-3 text-sm text-muted-foreground @[52rem]:order-0 @[52rem]:contents">
        <Fact label="Probability">
          <span className="tabular">{formatProbability(profile.probability)}</span>
        </Fact>
        <Fact label="Keys">
          <span className={keyCount === 0 ? "text-subtle-foreground" : undefined}>
            {keyCount === 0 ? "No keys" : `${keyCount} ${keyCount === 1 ? "key" : "keys"}`}
          </span>
        </Fact>
        <Fact label="Expires">
          <Expiry expiresAt={profile.expires_at} />
        </Fact>
      </div>
      <div className="order-2 justify-self-end @[52rem]:order-0 @[52rem]:justify-self-start">
        <Badge variant={badge.variant} size="sm">
          {badge.label}
        </Badge>
      </div>
      <div className="order-5 col-span-2 flex items-center justify-between gap-3 @[52rem]:order-0 @[52rem]:contents">
        <div className="flex items-center gap-2.5 @[52rem]:contents">
          <Switch
            checked={profile.enabled}
            disabled={!canWrite || toggle.isPending}
            aria-label={`Enable ${profile.name}`}
            onCheckedChange={(enabled) => {
              toggle.mutate(
                { profileId: profile.id, enabled },
                {
                  onError: (error) => {
                    toast.error(errorMessage(error));
                  },
                },
              );
            }}
          />
          <span aria-hidden className="text-sm font-medium text-foreground @[52rem]:hidden">
            {profile.enabled ? "On" : "Off"}
          </span>
        </div>
        {canWrite ? (
          <ProfileDialog
            profile={profile}
            trigger={
              <Button size="sm" aria-label={`Edit ${profile.name}`}>
                Edit
              </Button>
            }
          />
        ) : null}
      </div>
    </li>
  );
}

/** A value with its label above it on a narrow tile; the wide grid has column labels instead. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 @[52rem]:contents">
      <span className="text-overline text-subtle-foreground uppercase @[52rem]:sr-only">
        {label}
      </span>
      {children}
    </div>
  );
}

function Expiry({ expiresAt }: { expiresAt: string | null }) {
  if (expiresAt === null) {
    return <span className="text-subtle-foreground">Never</span>;
  }
  return (
    <span className="flex flex-col leading-tight">
      <RelativeTime iso={expiresAt} className="text-foreground" />
      <span aria-hidden className="text-xs text-subtle-foreground">
        {formatTimestamp(expiresAt)}
      </span>
    </span>
  );
}
