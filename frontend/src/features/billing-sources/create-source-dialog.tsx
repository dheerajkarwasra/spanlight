import { useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { BillingSource } from "@/lib/api";

import { CreateSourceForm } from "./create-source-form";

interface CreateSourceDialogProps {
  /** The element that opens the dialog: "Add source", or a row's Edit button. */
  trigger: ReactElement;
  /** The source to edit; absent when adding one. */
  source?: BillingSource;
  /** The server has no CREDENTIALS_KEYS: the API kinds are offered disabled. */
  apiKindsOff?: boolean;
  /** The server answered `409 NOT_CONFIGURED`; the page then shows `copy`, its notice. */
  onNotConfigured?: (copy: string) => void;
}

/**
 * The add and edit dialog. It is mounted only while open, so a typed key never outlives it, and
 * it refuses to close (Escape, Cancel, the close mark, an outside click) while a request runs.
 * Focus starts on the first field and returns to the trigger when it closes.
 */
export function CreateSourceDialog({
  trigger,
  source,
  apiKindsOff = false,
  onNotConfigured,
}: CreateSourceDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[560px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
          onOpenAutoFocus={(event) => {
            // The first field, not the close mark: the chosen kind when adding, the name when
            // editing (Figma focus order).
            const first =
              event.currentTarget instanceof HTMLElement
                ? event.currentTarget.querySelector<HTMLElement>(
                    '[role="radio"][data-state="checked"], input',
                  )
                : null;
            if (first) {
              event.preventDefault();
              first.focus();
            }
          }}
        >
          <CreateSourceForm
            source={source}
            apiKindsOff={apiKindsOff}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
            onNotConfigured={(copy) => {
              onNotConfigured?.(copy);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
