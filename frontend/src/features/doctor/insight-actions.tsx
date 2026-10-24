import { useRef, useState } from "react";
import { toast } from "sonner";

import { ReadOnlyNote } from "@/components/read-only-note";
import { Button } from "@/components/ui/button";
import type { InsightStatus } from "@/lib/api";

import { viewOnlyCopy } from "@/lib/permissions";

import { availableActions } from "./action-rules";
import { useAcknowledgeInsight, useResolveInsight, useUnmuteInsight } from "./doctor-queries";
import { actionErrorMessage } from "./insight-errors";
import { MuteDialog } from "./mute-dialog";

interface InsightActionsProps {
  insightId: string;
  status: InsightStatus;
  canManage: boolean;
}

/**
 * Figma "Doctor/Actions": Mute…, Resolve, Unmute and Acknowledge by status. Without
 * `insights:manage` they are hidden and one view-only note says who can act (the §7ag rule).
 * A failed action says why, inline.
 */
export function InsightActions({ insightId, status, canManage }: InsightActionsProps) {
  if (!canManage) {
    return <ReadOnlyNote>{viewOnlyCopy("insights", "insights:manage")}</ReadOnlyNote>;
  }
  return <ManageActions insightId={insightId} status={status} />;
}

function ManageActions({ insightId, status }: Omit<InsightActionsProps, "canManage">) {
  const [muting, setMuting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const acknowledge = useAcknowledgeInsight(insightId);
  const resolve = useResolveInsight(insightId);
  const unmute = useUnmuteInsight(insightId);
  const available = availableActions(status);
  const busy = acknowledge.isPending || resolve.isPending || unmute.isPending;

  function run(
    action: { mutate: (variables: undefined, options: MutateOptions) => void },
    done: string,
  ) {
    setFailure(null);
    action.mutate(undefined, {
      onSuccess: () => {
        toast.success(done);
        // The button that was clicked may be gone now; keep focus inside the actions.
        container.current?.focus();
      },
      onError: (error) => {
        setFailure(actionErrorMessage(error));
      },
    });
  }

  return (
    <div ref={container} tabIndex={-1} className="flex flex-col gap-2 outline-none lg:items-end">
      <div className="flex flex-wrap items-center gap-2.5 lg:flex-nowrap">
        {available.mute ? (
          <Button
            disabled={busy}
            onClick={() => {
              setFailure(null);
              setMuting(true);
            }}
          >
            Mute…
          </Button>
        ) : null}
        {available.unmute ? (
          <Button
            disabled={busy}
            loading={unmute.isPending}
            onClick={() => {
              run(unmute, "Unmuted the insight.");
            }}
          >
            Unmute
          </Button>
        ) : null}
        {available.resolve ? (
          <Button
            disabled={busy}
            loading={resolve.isPending}
            onClick={() => {
              run(resolve, "Resolved the insight.");
            }}
          >
            Resolve
          </Button>
        ) : null}
        {available.acknowledge ? (
          <Button
            variant="primary"
            disabled={busy}
            loading={acknowledge.isPending}
            onClick={() => {
              run(acknowledge, "Acknowledged the insight.");
            }}
          >
            Acknowledge
          </Button>
        ) : null}
      </div>
      {failure === null ? null : (
        <p role="alert" className="max-w-sm text-xs font-medium text-danger-text lg:text-right">
          {failure}
        </p>
      )}
      <MuteDialog insightId={insightId} open={muting} onOpenChange={setMuting} />
    </div>
  );
}

interface MutateOptions {
  onSuccess: () => void;
  onError: (error: unknown) => void;
}
