import { Lock, LockOpen, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import type { Dataset } from "@/lib/api";

import { datasetErrorMessage } from "./dataset-errors";
import { useLockDataset, useUnlockDataset } from "./dataset-queries";

/**
 * Figma "Repro 2 / 4": the Lock action with its confirmation, or Unlock, which acts at once. One
 * button swaps its label and keeps focus across the change (the dialog hands focus back to it on
 * close; Unlock refocuses it once the new state has loaded), and a polite live region announces it.
 */
export function DatasetLockAction({ dataset }: { dataset: Dataset }) {
  const locked = dataset.locked_at !== null;
  const lock = useLockDataset(dataset.id);
  const unlock = useUnlockDataset(dataset.id);
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  // Unlock disables the button while pending, which drops focus; take it back once unlocked.
  const refocus = useRef(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (refocus.current && !locked && !unlock.isPending) {
      refocus.current = false;
      buttonRef.current?.focus();
    }
  }, [locked, unlock.isPending]);

  function run(mutation: typeof lock, next: boolean) {
    refocus.current = !next;
    mutation.mutate(undefined, {
      onSuccess: () => {
        setOpen(false);
        setAnnouncement(next ? "Dataset locked." : "Dataset unlocked.");
      },
      onError: (error) => {
        refocus.current = false;
        toast.error(datasetErrorMessage(error));
      },
    });
  }

  return (
    <>
      <Button
        ref={buttonRef}
        loading={unlock.isPending}
        onClick={() => {
          if (locked) run(unlock, false);
          else setOpen(true);
        }}
      >
        {locked ? <LockOpen aria-hidden /> : <Lock aria-hidden />}
        {locked ? "Unlock" : "Lock"}
      </Button>
      <span className="sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!lock.isPending) setOpen(next);
        }}
      >
        <AlertDialogContent
          className="max-w-[440px] gap-5 rounded-card p-6 sm:p-7"
          onCloseAutoFocus={(event) => {
            // No Radix trigger here: return focus to the button, which now reads "Unlock" on success.
            event.preventDefault();
            buttonRef.current?.focus();
          }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <AlertDialogTitle className="[overflow-wrap:anywhere]">
                Lock “{dataset.name}”?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Locking blocks adding, uploading, editing and deleting items, and deleting the
                dataset. Experiments still run on it, and an admin can unlock it.
              </AlertDialogDescription>
            </div>
            {/* A plain button, so the footer Cancel is the only Radix Cancel and gets focus on open. */}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              disabled={lock.isPending}
              className="bg-surface-muted"
              onClick={() => {
                setOpen(false);
              }}
            >
              <X aria-hidden />
            </Button>
          </div>
          <AlertDialogFooter className="flex-row justify-end gap-2.5">
            <AlertDialogCancel disabled={lock.isPending}>Cancel</AlertDialogCancel>
            <Button
              variant="primary"
              loading={lock.isPending}
              onClick={() => {
                run(lock, true);
              }}
            >
              Lock dataset
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
