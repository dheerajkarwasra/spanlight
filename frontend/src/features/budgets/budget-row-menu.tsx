import { Ellipsis } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { errorMessage, type Budget } from "@/lib/api";

import { useDeleteBudget } from "./budgets-queries";

interface BudgetRowMenuProps {
  budget: Budget;
}

/** Figma "Budgets — List — row menu": Delete, behind the shared delete confirmation. */
export function BudgetRowMenu({ budget }: BudgetRowMenuProps) {
  const [deleting, setDeleting] = useState(false);
  const deleteBudget = useDeleteBudget();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="secondary"
            size="icon-sm"
            aria-label={`More actions for ${budget.name}`}
            className="border-border-strong shadow-none"
          >
            <Ellipsis aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmDialog
        control={{ open: deleting, onOpenChange: setDeleting }}
        title={`Delete ${budget.name}?`}
        description={
          budget.action === "block"
            ? "Its alerts stop, and gateway calls in its scope are no longer blocked by it. This can't be undone."
            : "Its alerts stop. This can't be undone."
        }
        confirmLabel="Delete budget"
        onConfirm={async () => {
          try {
            await deleteBudget.mutateAsync(budget.id);
            toast.success(`Deleted budget "${budget.name}".`);
          } catch (error) {
            toast.error(errorMessage(error));
            throw error;
          }
        }}
      />
    </>
  );
}
