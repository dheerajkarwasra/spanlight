import { useRef, useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { Span } from "@/lib/api";

import { AddToQueueForm } from "./add-to-queue-form";
import type { TraceRef } from "./span-pick";

interface AddToQueueDialogProps {
  traces: TraceRef[];
  /** The span selected in the trace's waterfall; offered as "Selected span" for one trace. */
  selectedSpan?: Span | null;
  trigger: ReactElement;
  /** Runs when the dialog closes after the traces were queued, e.g. to clear the selection. */
  onAdded?: () => void;
}

/** Figma "Add to queue dialog": queue a trace (or one of its spans) for a person to score. */
export function AddToQueueDialog({
  traces,
  selectedSpan = null,
  trigger,
  onAdded,
}: AddToQueueDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const added = useRef(false);
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
          className="max-w-[520px] rounded-card p-6 sm:p-7"
          onCloseAutoFocus={(event) => {
            if (added.current && onAdded) {
              added.current = false;
              event.preventDefault();
              onAdded();
            }
          }}
        >
          <AddToQueueForm
            traces={traces}
            selectedSpan={traces.length === 1 ? selectedSpan : null}
            onPendingChange={setPending}
            onDone={() => {
              added.current = true;
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
