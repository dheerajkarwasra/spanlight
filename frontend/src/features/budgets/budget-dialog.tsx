import { useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { Budget } from "@/lib/api";

import { BudgetForm } from "./budget-form";
import type { BudgetFormValues } from "./budget-schema";

interface BudgetDialogProps {
  /** Null to create a budget; a budget to edit it. */
  budget: Budget | null;
  /** The button that opens the dialog: Create budget, or the row's Edit. */
  trigger: ReactElement;
  /** A create dialog's prefill (spec ruling 12). When given, the dialog starts open. */
  initial?: Partial<BudgetFormValues>;
  /** Called whenever the dialog closes (cancelled or saved). */
  onClose?: () => void;
}

/** Figma "Budgets — Budget dialog" (every scope, validation errors). Mounted only while open. */
export function BudgetDialog({ budget, trigger, initial, onClose }: BudgetDialogProps) {
  const [open, setOpen] = useState(initial !== undefined);
  const [pending, setPending] = useState(false);
  const change = (next: boolean) => {
    setOpen(next);
    if (!next) {
      onClose?.();
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Escape, Cancel, the close mark and an outside click may not close it mid-save.
        if (next || !pending) {
          change(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[640px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <BudgetForm
            budget={budget}
            initial={initial}
            onPendingChange={setPending}
            onSaved={() => {
              change(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
