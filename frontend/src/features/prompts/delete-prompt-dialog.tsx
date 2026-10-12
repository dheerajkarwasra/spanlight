import { useNavigate } from "@tanstack/react-router";
import { X } from "lucide-react";
import { AlertDialog as AlertDialogPrimitive } from "radix-ui";
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
import { useProjectParams } from "@/features/shell";
import { pluralNoun } from "@/lib/format";
import { useReturnFocus } from "@/lib/use-return-focus";

import { isProblem, PROMPT_IN_USE_MESSAGE, promptErrorMessage } from "./prompt-errors";
import { useDeletePrompt } from "./prompt-queries";

interface DeletePromptDialogProps {
  name: string;
  versionCount: number;
  labelCount: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function labelPhrase(count: number): string {
  if (count === 1) {
    return "its label";
  }
  return count === 2 ? "both labels" : `all ${String(count)} ${pluralNoun(count, "label")}`;
}

function removalSummary(versionCount: number, labelCount: number): string {
  const versions = versionCount === 1 ? "its version" : `all ${String(versionCount)} versions`;
  const labels = labelCount === 0 ? "" : ` and ${labelPhrase(labelCount)}`;
  return `This removes ${versions}${labels}. Traces keep the version id they recorded.`;
}

/**
 * Figma "Prompts — Detail — delete blocked": the delete confirmation. While an evaluator or an
 * experiment uses a version (`409 PROMPT_IN_USE`), it explains why and keeps Delete off.
 */
export function DeletePromptDialog(props: DeletePromptDialogProps) {
  const { name, open, onOpenChange } = props;
  const remove = useDeletePrompt(name);
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const [failure, setFailure] = useState<{ message: string; blocked: boolean } | null>(null);
  const returnFocus = useReturnFocus();

  async function confirm() {
    setFailure(null);
    try {
      await remove.mutateAsync();
      toast.success(`Deleted ${name}.`);
      onOpenChange(false);
      void navigate({ to: "/$orgId/$projectId/prompts", params: { orgId, projectId } });
    } catch (error) {
      const blocked = isProblem(error, "PROMPT_IN_USE");
      setFailure({ message: blocked ? PROMPT_IN_USE_MESSAGE : promptErrorMessage(error), blocked });
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!remove.isPending) {
          setFailure(null);
          onOpenChange(next);
        }
      }}
    >
      <AlertDialogContent className="max-w-[480px] gap-5 rounded-card p-6 sm:p-7" {...returnFocus}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <AlertDialogTitle className="[overflow-wrap:anywhere]">Delete {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {removalSummary(props.versionCount, props.labelCount)}
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
        {failure === null ? null : (
          <Callout tone="danger" role="alert">
            {failure.message}
          </Callout>
        )}
        <AlertDialogFooter className="flex-row justify-end gap-2.5">
          <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
          <Button
            variant="danger"
            loading={remove.isPending}
            disabled={failure?.blocked === true}
            onClick={() => void confirm()}
          >
            Delete prompt
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
