import { Trash2 } from "lucide-react";
import { useState } from "react";

import { Callout } from "@/components/callout";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { isApiError, type AnnotationQueue } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { queueErrorMessage } from "./annotation-errors";
import { useDeleteQueue } from "./annotation-queries";

function deleteSummary({ pending, done, skipped }: AnnotationQueue["progress"]): string {
  const items = pending + done + skipped;
  const lead =
    items === 0
      ? "It has no items."
      : `Its ${pluralize(items, "item")} ${items === 1 ? "leaves" : "leave"} review. Scores already saved stay on their traces.`;
  return `${lead} This can't be undone.`;
}

/**
 * Figma "E7 — Delete queue" confirm. Items go with the queue; scores live on traces and stay. The
 * server refuses (`409 QUEUE_IN_USE`) while a review policy fills the queue.
 */
export function DeleteQueueDialog({ queue }: { queue: AnnotationQueue }) {
  const [open, setOpen] = useState(false);
  const remove = useDeleteQueue();
  const inUse = isApiError(remove.error) && remove.error.code === "QUEUE_IN_USE";

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (next || !remove.isPending) {
          setOpen(next);
          if (!next) remove.reset();
        }
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="ghost" className="text-danger-text">
          <Trash2 aria-hidden />
          Delete queue
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent className="max-w-[440px] gap-5 rounded-card p-6 sm:p-7">
        <div className="flex flex-col gap-1.5">
          <AlertDialogTitle className="[overflow-wrap:anywhere]">
            Delete queue “{queue.name}”?
          </AlertDialogTitle>
          <AlertDialogDescription>{deleteSummary(queue.progress)}</AlertDialogDescription>
        </div>
        {remove.error ? (
          <Callout tone="danger" role="alert">
            {queueErrorMessage(remove.error)}
          </Callout>
        ) : null}
        <AlertDialogFooter className="flex-row justify-end gap-2.5">
          <AlertDialogCancel disabled={remove.isPending}>
            {inUse ? "Close" : "Cancel"}
          </AlertDialogCancel>
          <Button
            variant="danger"
            loading={remove.isPending}
            disabled={inUse}
            onClick={() => {
              remove.mutate(queue);
            }}
          >
            Delete queue
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
