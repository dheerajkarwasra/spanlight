import { useState, type ReactElement } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { useMe } from "@/features/auth";
import {
  DialogHeading,
  EXPORT_DIALOG_CLASS,
  ExportsNotConfigured,
  ExportQueued,
  SubmitError,
  blockWhile,
  exportFormatLabel,
  exportWindowProblem,
  formatTraceCount,
  useExportSubmission,
} from "@/features/exports";
import type { ExportFormat, TraceSummary } from "@/lib/api";

import { ExportFormatPicker } from "./export-format-picker";
import { ExportSummary } from "./export-summary";
import { useExportDraft } from "./use-export-draft";

interface ExportDialogProps {
  /** The element that opens the dialog: the Export button. */
  trigger: ReactElement;
  /** The traces ticked in the list ("Export selection"); without it, the list's filters. */
  selection?: readonly TraceSummary[];
}

/**
 * Export traces (Figma "Traces/Export dialog"): pick a format, check what will be exported, start
 * it. A centred dialog from 640 px, a bottom sheet below. The content is mounted only while the
 * dialog is open, so every opening starts fresh: the filters are read again and the submission gets
 * a new idempotency key.
 */
export function ExportDialog({ trigger, selection }: ExportDialogProps) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <ExportDialogContent
          selection={selection}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </Dialog>
  );
}

function ExportDialogContent({
  selection,
  onClose,
}: {
  selection: readonly TraceSummary[] | undefined;
  onClose: () => void;
}) {
  const live = useExportDraft(selection);
  // What is sent is frozen at opening, so the summary and the request cannot drift apart when the
  // list's "now" moves on; the row count may still arrive afterwards.
  const [filters] = useState(live.filters);
  const draft = { ...live, filters };

  const [format, setFormat] = useState<ExportFormat>("jsonl");
  const submission = useExportSubmission();
  const exportsConfigured = useMe().capabilities.exports_configured;
  const problem = exportWindowProblem(filters);

  return (
    <DialogContent
      hideClose
      // While the POST is out, closing would drop its idempotency key: a second submit after a
      // reopen could then queue a duplicate. Esc, a click outside and both buttons wait for it.
      onEscapeKeyDown={blockWhile(submission.isPending)}
      onInteractOutside={blockWhile(submission.isPending)}
      className={EXPORT_DIALOG_CLASS}
    >
      {submission.created ? (
        <>
          <DialogHeading title="Export queued" />
          <ExportQueued title={queuedTitle(format, draft.rows)} onDone={onClose} />
        </>
      ) : (
        <>
          <DialogHeading
            title="Export traces"
            description={`Create a file of ${selection ? "the selected traces" : "the traces that match your current filters"}. You’ll download it from Settings › Exports.`}
            closeDisabled={submission.isPending}
          />
          <ExportFormatPicker value={format} onChange={setFormat} />
          <ExportSummary draft={draft} problem={problem} />
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
              disabled={problem !== null || !exportsConfigured}
              onClick={() => {
                submission.submit({ format, filters });
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

/** "12,904 traces · CSV", or "CSV export" when the count was not known. */
function queuedTitle(format: ExportFormat, rows: number | null): string {
  const formatName = exportFormatLabel(format);
  return rows === null ? `${formatName} export` : `${formatTraceCount(rows)} · ${formatName}`;
}
