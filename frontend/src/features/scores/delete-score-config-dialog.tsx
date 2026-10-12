import { X } from "lucide-react";
import { AlertDialog as AlertDialogPrimitive } from "radix-ui";
import { useState, type ReactElement } from "react";
import { toast } from "sonner";

import { Notice } from "@/components/notice";
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
import type { ScoreConfig } from "@/lib/api";

import { isConfigInUse, scoreConfigErrorMessage } from "./score-errors";
import { useDeleteScoreConfig } from "./scores-queries";

interface DeleteScoreConfigDialogProps {
  config: ScoreConfig;
  trigger: ReactElement;
}

/**
 * Figma "Score configs — Delete unused" and "Delete conflict (in use)". Whether scores use the
 * name is only known when the delete is tried, so a refusal turns the dialog into the in-use
 * state: the reason, Close, and the delete button disabled.
 */
export function DeleteScoreConfigDialog({ config, trigger }: DeleteScoreConfigDialogProps) {
  const [open, setOpen] = useState(false);
  const remove = useDeleteScoreConfig();
  const inUse = isConfigInUse(remove.error);

  function confirm() {
    remove.mutate(config.id, {
      onSuccess: () => {
        toast.success(`Deleted the ${config.name} score config.`);
        setOpen(false);
      },
    });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (remove.isPending) return;
        if (next) remove.reset();
        setOpen(next);
      }}
    >
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent className="max-w-[440px] gap-5 rounded-card p-6 sm:p-7">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <AlertDialogTitle className="[overflow-wrap:anywhere]">
              Delete “{config.name}”?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This removes the config. Deleting it cannot be undone.
            </AlertDialogDescription>
          </div>
          <AlertDialogPrimitive.Cancel asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              disabled={remove.isPending}
              className="bg-surface-muted"
            >
              <X aria-hidden />
            </Button>
          </AlertDialogPrimitive.Cancel>
        </div>
        {inUse ? (
          <Notice tone="danger" role="alert">
            <p className="font-semibold">This config is in use</p>
            <p>
              Scores use {config.name}, so it cannot be deleted. Delete those scores first, or keep
              the config and edit its range or values.
            </p>
          </Notice>
        ) : remove.error ? (
          <Notice tone="danger" role="alert">
            {scoreConfigErrorMessage(remove.error, config.name)}
          </Notice>
        ) : (
          <Notice>
            Scores sent with this name later will create a new config from their first value.
          </Notice>
        )}
        <AlertDialogFooter className="flex-row justify-end gap-2.5">
          <AlertDialogCancel disabled={remove.isPending}>
            {inUse ? "Close" : "Cancel"}
          </AlertDialogCancel>
          <Button variant="danger" loading={remove.isPending} disabled={inUse} onClick={confirm}>
            Delete config
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
