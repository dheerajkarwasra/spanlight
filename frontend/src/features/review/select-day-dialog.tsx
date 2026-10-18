import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { dayOptionLabel } from "./policy-summary";
import { useRequestRun } from "./review-queries";
import { reviewErrorMessage, selectableDays } from "./setup-steps";

/**
 * "Select a day…": runs the selection by hand for one completed UTC day, yesterday back to 7 days
 * ago, e.g. a night the policy was paused.
 */
export function SelectDayDialog({ trigger }: { trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const run = useRequestRun();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!run.isPending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <SelectDayForm
          run={run}
          onDone={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </Dialog>
  );
}

interface SelectDayFormProps {
  run: ReturnType<typeof useRequestRun>;
  onDone: () => void;
}

function SelectDayForm({ run, onDone }: SelectDayFormProps) {
  const [days] = useState(() => selectableDays(new Date()));
  const [day, setDay] = useState(days[0] ?? "");
  const [failure, setFailure] = useState<unknown>(null);

  async function submit() {
    setFailure(null);
    try {
      await run.mutateAsync(day);
      toast.success(`Selecting ${dayOptionLabel(day)}. The traces appear in the queue shortly.`);
      onDone();
    } catch (error) {
      setFailure(error);
    }
  }

  return (
    <DialogContent hideClose className="max-w-[440px] gap-5 rounded-card p-6 sm:p-7">
      <DialogHeading
        title="Select a day"
        description="Queue the traces that ended on a past UTC day. Each day is selected once."
        showClose={run.isPending ? "disabled" : true}
      />
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <fieldset className="flex flex-col gap-1.5">
          <legend className="sr-only">Day</legend>
          {days.map((option) => (
            <label
              key={option}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-input border px-3 py-2.5 text-sm font-medium",
                option === day
                  ? "border-accent bg-surface-selected"
                  : "border-transparent bg-surface-muted",
              )}
            >
              <input
                type="radio"
                name="review-day"
                value={option}
                checked={option === day}
                onChange={() => setDay(option)}
                className="accent-[var(--accent)]"
              />
              {dayOptionLabel(option)}
            </label>
          ))}
        </fieldset>
        {failure === null ? null : (
          <Callout tone="danger" role="alert">
            {reviewErrorMessage(failure)}
          </Callout>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={run.isPending}>Cancel</Button>
          </DialogClose>
          <Button type="submit" variant="primary" loading={run.isPending}>
            Select day
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}
