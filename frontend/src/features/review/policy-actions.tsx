import { CalendarDays, Pause, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import type { ReviewPolicy } from "@/lib/api";

import { policyInput } from "./policy-summary";
import { useDeletePolicy, useSavePolicy } from "./review-queries";
import { SelectDayDialog } from "./select-day-dialog";
import { reviewErrorMessage } from "./setup-steps";

/** The policy's writes: a manual run, pause or resume, delete. Members and up only. */
export function PolicyActions({ policy }: { policy: ReviewPolicy }) {
  const save = useSavePolicy();
  const remove = useDeletePolicy();

  function toggleEnabled() {
    const enabled = !policy.enabled;
    save.mutate(policyInput(policy, { enabled }), {
      onSuccess: () => {
        toast.success(enabled ? "Daily review resumed." : "Daily review paused.");
      },
      onError: (error) => {
        toast.error(reviewErrorMessage(error));
      },
    });
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <SelectDayDialog
        trigger={
          <Button>
            <CalendarDays aria-hidden />
            Select a day…
          </Button>
        }
      />
      <Button
        variant={policy.enabled ? "secondary" : "primary"}
        loading={save.isPending}
        onClick={toggleEnabled}
      >
        {policy.enabled ? <Pause aria-hidden /> : <Play aria-hidden />}
        {policy.enabled ? "Pause" : "Resume"}
      </Button>
      <ConfirmDialog
        trigger={
          <Button className="text-danger-text">
            <Trash2 aria-hidden />
            Delete policy
          </Button>
        }
        title="Delete the daily review policy?"
        description="Nightly selection stops. The queue, its items and the selection history stay."
        confirmLabel="Delete policy"
        onConfirm={async () => {
          try {
            await remove.mutateAsync();
            toast.success("Daily review policy deleted.");
          } catch (error) {
            toast.error(reviewErrorMessage(error));
            throw error;
          }
        }}
      />
    </div>
  );
}
