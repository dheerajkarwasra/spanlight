import { Download, Table } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";
import { useMe } from "@/features/auth";
import {
  DialogHeading,
  EXPORT_DIALOG_CLASS,
  ExportsNotConfigured,
  ExportQueued,
  SubmitError,
  blockWhile,
  useExportSubmission,
} from "@/features/exports";
import { usePermission } from "@/features/shell/project-context";

import {
  attributionExportTitle,
  toAttributionExportInput,
  type AttributionExportView,
} from "./attribution-export";
import { AttributionExportSummary } from "./attribution-export-summary";

interface AttributionExportButtonProps {
  view: AttributionExportView;
  /** Off while the page holds its fetch: no key yet, or a key the rollup does not hold. */
  disabled: boolean;
}

/**
 * The Attribution page's Export button and its dialog (Figma "Attribution / Export dialog"):
 * format fixed to CSV, the view's window, group and environment echoed, then the shared queued
 * state. Members and above can export; for a viewer the button is off, with the reason on hover.
 */
export function AttributionExportButton({ view, disabled }: AttributionExportButtonProps) {
  const canExport = usePermission("export:create");
  const [open, setOpen] = useState(false);
  const button = (
    <Button variant="secondary" size="lg" disabled={disabled || !canExport}>
      <Download aria-hidden />
      Export
    </Button>
  );

  if (!canExport) {
    const reason = "Viewers can't export attribution. Ask an admin for access.";
    return (
      <Tooltip content={reason}>
        <span tabIndex={0} className="inline-flex rounded-full">
          {button}
          <span className="sr-only">{reason}</span>
        </span>
      </Tooltip>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{button}</DialogTrigger>
      {/* Mounted only while open, so each opening reads the view again and gets a new key. */}
      {open ? (
        <AttributionExportContent
          view={view}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </Dialog>
  );
}

function AttributionExportContent({
  view: live,
  onClose,
}: {
  view: AttributionExportView;
  onClose: () => void;
}) {
  // Frozen at opening, so the summary and the request cannot drift apart as the page refetches.
  const [view] = useState(live);
  const submission = useExportSubmission();
  const exportsConfigured = useMe().capabilities.exports_configured;

  return (
    <DialogContent
      hideClose
      // While the POST is out, closing would drop its idempotency key and a retry could queue a
      // duplicate, so Esc, a click outside and both buttons wait for it.
      onEscapeKeyDown={blockWhile(submission.isPending)}
      onInteractOutside={blockWhile(submission.isPending)}
      className={EXPORT_DIALOG_CLASS}
    >
      {submission.created ? (
        <>
          <DialogHeading title="Export queued" />
          <ExportQueued title={attributionExportTitle(view)} onDone={onClose} />
        </>
      ) : (
        <>
          <DialogHeading
            title="Export attribution"
            description="Create a CSV of this table. You’ll download it from Settings › Exports."
            closeDisabled={submission.isPending}
          />
          <div className="flex flex-col gap-2">
            <p className="text-label leading-5 font-semibold text-foreground">Format</p>
            <div className="flex items-center gap-3 rounded-tile border-[1.5px] border-accent bg-surface-selected py-3.5 pr-4 pl-3.5">
              <span
                aria-hidden
                className="flex size-9 shrink-0 items-center justify-center rounded-input bg-surface"
              >
                <Table className="size-[18px] text-accent" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-sm font-semibold text-foreground">CSV</span>
                <span className="text-xs leading-[1.4] font-medium text-muted-foreground">
                  One row per value, then the no-key, folded and total rows. Attribution exports are
                  CSV only.
                </span>
              </span>
            </div>
          </div>
          <AttributionExportSummary view={view} />
          {exportsConfigured ? null : <ExportsNotConfigured />}
          {submission.error ? <SubmitError error={submission.error} /> : null}
          <div className="flex items-center justify-end gap-2.5 max-sm:[&>*]:flex-1">
            <DialogClose asChild>
              <Button variant="secondary" disabled={submission.isPending}>
                Cancel
              </Button>
            </DialogClose>
            <Button
              variant="primary"
              loading={submission.isPending}
              disabled={!exportsConfigured}
              onClick={() => {
                submission.submit(toAttributionExportInput(view));
              }}
            >
              Start export
            </Button>
          </div>
        </>
      )}
    </DialogContent>
  );
}
