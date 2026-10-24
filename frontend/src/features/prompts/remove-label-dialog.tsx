import { useState } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useReturnFocus } from "@/lib/use-return-focus";

import { promptErrorMessage } from "./prompt-errors";
import { useDeleteLabel } from "./prompt-queries";

interface RemoveLabelDialogProps {
  name: string;
  /** The label to remove; null while closed. */
  label: string | null;
  onClose: () => void;
}

/**
 * Figma "P1 — Remove label" confirm. SDK clients fetch a prompt by label, so removing one breaks
 * them until it is set again; focus starts on Cancel.
 */
export function RemoveLabelDialog({ name, label, onClose }: RemoveLabelDialogProps) {
  const remove = useDeleteLabel(name);
  const [failure, setFailure] = useState<string | null>(null);
  const returnFocus = useReturnFocus();

  async function confirm(target: string) {
    setFailure(null);
    try {
      await remove.mutateAsync(target);
      toast.success(`Removed label ${target}.`);
      onClose();
    } catch (error) {
      setFailure(promptErrorMessage(error));
    }
  }

  return (
    <AlertDialog
      open={label !== null}
      onOpenChange={(open) => {
        if (!open && !remove.isPending) {
          setFailure(null);
          onClose();
        }
      }}
    >
      {label === null ? null : (
        <AlertDialogContent
          className="max-w-[440px] gap-5 rounded-card p-6 sm:p-7"
          {...returnFocus}
        >
          <div className="flex flex-col gap-1.5">
            <AlertDialogTitle className="[overflow-wrap:anywhere]">
              Remove label {label}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Clients that fetch <code className="font-mono text-foreground">{label}</code> will get
              an error until you set it again.
            </AlertDialogDescription>
          </div>
          {failure === null ? null : (
            <Callout tone="danger" role="alert">
              {failure}
            </Callout>
          )}
          <AlertDialogFooter className="flex-row justify-end gap-2.5">
            <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
            <Button variant="danger" loading={remove.isPending} onClick={() => void confirm(label)}>
              Remove label
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      )}
    </AlertDialog>
  );
}
