import { FlaskConical } from "lucide-react";

import { ReadOnlyNote } from "@/components/read-only-note";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { usePermission } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";

import { GatewayLayout } from "../gateway-layout";
import { LabRuns } from "./lab-runs";
import { ProfileDialog } from "./profile-dialog";
import { ProfileList } from "./profile-list";

const LAB_INTRO =
  "Inject the failures real providers produce, so you can see how your app copes. Faults only run on non-production keys.";

/** Figma "Gateway — Lab": the intro, the fault profiles and the recent faulted calls. */
export function GatewayLabPage() {
  const canWrite = usePermission("gateway:write");

  return (
    <GatewayLayout>
      <div className="flex flex-col gap-6">
        {canWrite ? null : (
          <ReadOnlyNote>{viewOnlyCopy("fault profiles", "gateway:write")}</ReadOnlyNote>
        )}
        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:gap-5 sm:p-6">
          <span
            aria-hidden
            className="flex size-12 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent"
          >
            <FlaskConical className="size-6" strokeWidth={1.75} />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <h2 className="text-card">Integration Lab</h2>
            <p className="text-sm text-muted-foreground">{LAB_INTRO}</p>
          </div>
          {canWrite ? (
            <ProfileDialog
              trigger={
                <Button variant="primary" className="max-sm:w-full">
                  New fault profile
                </Button>
              }
            />
          ) : null}
        </Card>
        <ProfileList canWrite={canWrite} />
        <LabRuns />
      </div>
    </GatewayLayout>
  );
}
