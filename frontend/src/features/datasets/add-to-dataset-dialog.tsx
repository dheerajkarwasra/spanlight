import { useRef, useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { Span, TraceDetail } from "@/lib/api";

import { AddTracesForm } from "./add-traces-form";
import { AddTraceForm } from "./add-trace-form";
import type { TraceRef } from "./span-pick";

/** One open trace (with the span selected in its waterfall), or traces selected in the list. */
export type AddTarget =
  | { kind: "trace"; trace: TraceDetail; selectedSpan: Span | null }
  | { kind: "traces"; traces: TraceRef[] };

interface AddToDatasetDialogProps {
  target: AddTarget;
  trigger: ReactElement;
  /**
   * Runs when the dialog closes after items were added (not before, so a bulk result with
   * rejected traces stays readable), e.g. to clear the list selection and place focus.
   */
  onAdded?: () => void;
}

/**
 * Figma "Add to dataset dialog" (single, rejected, bulk): saves a trace's span input and output as
 * dataset items. The recorded output is only a starting point for the expected answer.
 */
export function AddToDatasetDialog({ target, trigger, onAdded }: AddToDatasetDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const added = useRef(false);
  const close = () => {
    setOpen(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) setOpen(next);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[520px] overflow-y-auto rounded-card p-6 sm:p-7"
          onCloseAutoFocus={(event) => {
            if (added.current && onAdded) {
              added.current = false;
              event.preventDefault();
              onAdded();
            }
          }}
        >
          {target.kind === "trace" ? (
            <AddTraceForm
              trace={target.trace}
              selectedSpan={target.selectedSpan}
              onPendingChange={setPending}
              onDone={close}
            />
          ) : (
            <AddTracesForm
              traces={target.traces}
              onPendingChange={setPending}
              onAdded={() => {
                added.current = true;
              }}
              onDone={close}
            />
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
