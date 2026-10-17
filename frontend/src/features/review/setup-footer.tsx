import { toast } from "sonner";

import { Button } from "@/components/ui/button";

import { useRequestRun } from "./review-queries";
import { reviewErrorMessage, selectableDays } from "./setup-steps";
import type { SetupRun } from "./use-setup-review";

interface SetupFooterProps {
  /** The edited step (0 to 2), the first one shown, and whether it can move on. */
  position: { step: number; first: number; valid: boolean };
  /** The confirm's run while its progress is shown; null while editing. */
  run: SetupRun | null;
  onBack: () => void;
  onRetry: () => void;
  onClose: () => void;
}

/** The set-up dialog's buttons for each phase: editing, running, failed, done. */
export function SetupFooter({ position, run, onBack, onRetry, onClose }: SetupFooterProps) {
  const selectDay = useRequestRun();
  const running = run?.steps.some((entry) => entry.status === "running") ?? false;
  const { step, first, valid } = position;

  async function selectYesterday() {
    try {
      await selectDay.mutateAsync(selectableDays(new Date())[0] ?? "");
      toast.success("Selecting yesterday's traces. They appear in the queue shortly.");
      onClose();
    } catch (error) {
      toast.error(reviewErrorMessage(error));
    }
  }

  if (run?.done) {
    return (
      <div className="flex flex-wrap justify-end gap-2.5">
        <Button type="button" variant="ghost" disabled={selectDay.isPending} onClick={onClose}>
          Close
        </Button>
        <Button
          type="button"
          variant="primary"
          loading={selectDay.isPending}
          onClick={() => void selectYesterday()}
        >
          Select yesterday now
        </Button>
      </div>
    );
  }
  if (run !== null) {
    return (
      <div className="flex flex-wrap justify-end gap-2.5">
        <Button type="button" variant="ghost" disabled={running} onClick={onBack}>
          Back
        </Button>
        <Button type="button" variant="primary" loading={running} onClick={onRetry}>
          {running ? "Setting up…" : "Try again"}
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-2.5">
      <p className="text-xs font-medium text-muted-foreground">Step {step + 1} of 3</p>
      <div className="flex gap-2.5">
        <Button type="button" variant="ghost" onClick={step > first ? onBack : onClose}>
          {step > first ? "Back" : "Cancel"}
        </Button>
        <Button type="submit" variant="primary" disabled={!valid}>
          {step < 2 ? "Next" : "Create daily review"}
        </Button>
      </div>
    </div>
  );
}
