import { useRef, useState, type ReactElement } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { errorMessage, type BillingSource } from "@/lib/api";

import { useDeleteSource } from "./billing-sources-queries";

interface DeleteSourceDialogProps {
  source: BillingSource;
  /** The row's delete icon button. */
  trigger: ReactElement;
}

/**
 * Figma "Billing sources / delete": the typed-name confirmation. Sources have no slug, so the name
 * is typed exactly as the list shows it; Delete stays disabled until it matches. The field has the
 * focus when the dialog opens.
 */
export function DeleteSourceDialog({ source, trigger }: DeleteSourceDialogProps) {
  const deleteSource = useDeleteSource();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const deleted = useRef(false);
  const pending = deleteSource.isPending;
  const matches = typed === source.name;

  function handleOpenChange(next: boolean) {
    if (pending) {
      return;
    }
    setOpen(next);
    if (!next) {
      setTyped("");
    }
  }

  function confirm() {
    if (!matches || pending) {
      return;
    }
    deleteSource.mutate(source.id, {
      onSuccess: () => {
        toast.success(`Deleted billing source "${source.name}".`);
        deleted.current = true;
        setOpen(false);
      },
      onError: (error) => {
        toast.error(errorMessage(error));
      },
    });
  }

  const deleteButton = (
    <Button type="submit" variant="danger" disabled={!matches} loading={pending}>
      Delete source
    </Button>
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        hideClose
        className="max-w-[560px] gap-5 rounded-card p-5 sm:p-7"
        onOpenAutoFocus={(event) => {
          opener.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
          event.preventDefault();
          inputRef.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          // The deleted source's row, delete button included, goes once the list reloads: move
          // focus to the card's heading first, then reload, so focus never falls to the page
          // (WCAG 2.4.3).
          const heading = opener.current
            ?.closest('[role="region"]')
            ?.querySelector<HTMLElement>("h2");
          if (deleted.current && heading) {
            event.preventDefault();
            heading.tabIndex = -1;
            heading.focus();
          }
          if (deleted.current) {
            void deleteSource.reload();
          }
        }}
      >
        <DialogHeading
          title={`Delete ${source.name}?`}
          description="Deletes the source, its fetched cost lines and its run history. Reconciliation reports for it stop working. This can't be undone."
          showClose={pending ? "disabled" : true}
        />
        <form
          noValidate
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            confirm();
          }}
        >
          <FormField
            label={`Type ${source.name} to confirm`}
            hint="The source's name, exactly as shown in the list."
          >
            <Input
              ref={inputRef}
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              readOnly={pending}
            />
          </FormField>
          <DialogFooter className="flex-row justify-end gap-2.5">
            <Button disabled={pending} onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            {matches ? (
              deleteButton
            ) : (
              <DisabledReason reason={`Type ${source.name} to confirm.`}>
                {deleteButton}
              </DisabledReason>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
