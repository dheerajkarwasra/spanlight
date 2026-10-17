import { Link } from "@tanstack/react-router";
import { ArrowUpRight, CircleCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DialogDescription } from "@/components/ui/dialog";
import { useProjectParams } from "@/features/shell/project-context";

interface ExportQueuedProps {
  /** What was requested, e.g. "12,904 traces · CSV" or "customer_id · 30 days · CSV". */
  title: string;
  onDone: () => void;
}

/**
 * The dialog after the server accepted the export (Figma "Export queued"): a success tile with
 * what was requested, where it will appear and how long it lasts, a link to Settings › Exports and
 * "Done". The file is built in the background, so this does not wait for it.
 */
export function ExportQueued({ title, onDone }: ExportQueuedProps) {
  const { orgId, projectId } = useProjectParams();

  return (
    <>
      <div className="flex items-start gap-3.5 rounded-tile bg-success-subtle px-[18px] py-4">
        <span
          aria-hidden
          className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface"
        >
          <CircleCheck className="size-[18px] text-success" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
          <p role="status" className="font-semibold text-foreground">
            {title}
          </p>
          <DialogDescription className="text-sm">
            We&apos;re building the file in the background. It appears in Settings › Exports when
            it&apos;s ready, and expires 7 days later.
          </DialogDescription>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3">
        <Link
          to="/$orgId/$projectId/settings/exports"
          params={{ orgId, projectId }}
          onClick={onDone}
          className="inline-flex items-center gap-1 rounded-sm text-sm font-semibold text-accent underline-offset-4 hover:underline"
        >
          View exports
          <ArrowUpRight aria-hidden className="size-4" />
        </Link>
        <Button variant="primary" onClick={onDone}>
          Done
        </Button>
      </div>
    </>
  );
}
