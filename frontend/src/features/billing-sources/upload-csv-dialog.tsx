import { useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { BillingSource } from "@/lib/api";

import { UploadCsvForm } from "./upload-csv-form";

interface UploadCsvDialogProps {
  /** A CSV-kind source. */
  source: BillingSource;
  /** The row's Upload button, or the reconciliation page's Upload CSV. */
  trigger: ReactElement;
}

/**
 * Figma "Reconciliation / csv upload dialog" and "/ csv errors": choose the provider's cost CSV
 * and upload it as one run. A refused file lists the importer's reasons by row and column, and
 * nothing is stored until the whole file is valid. Mounted only while open.
 */
export function UploadCsvDialog({ source, trigger }: UploadCsvDialogProps) {
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
            // "browse", the dialog's first control, rather than the close mark.
            const browse =
              event.currentTarget instanceof HTMLElement
                ? event.currentTarget.querySelector<HTMLElement>("[data-browse]")
                : null;
            if (browse) {
              event.preventDefault();
              browse.focus();
            }
          }}
        >
          <UploadCsvForm
            source={source}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
