import { useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { Budget } from "@/lib/api";

import { BudgetForm } from "./budget-form";

interface BudgetDialogProps {
  /** Null to create a budget; a budget to edit it. */
  budget: Budget | null;
  /** The button that opens the dialog: Create budget, or the row's Edit. */
  trigger: ReactElement;
}

/** Figma "Budgets — Budget dialog" (every scope, validation errors). Mounted only while open. */
export function BudgetDialog({ budget, trigger }: BudgetDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Escape, Cancel, the close mark and an outside click may not close it mid-save.
        if (next || !pending) {
          setOpen(next);
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
            onPendingChange={setPending}
            onSaved={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
