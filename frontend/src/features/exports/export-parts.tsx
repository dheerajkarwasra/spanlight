import { CircleAlert, Download } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ExportFormat, ExportStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

import { exportFormatLabel } from "./export-display";
import { useDownloadExport } from "./export-queries";
import { EXPORT_STATUS_LABELS, exportFailure } from "./export-status";

/** Figma "Settings/Scope tag": the format in mono, in a hairline pill. */
export function FormatTag({ format }: { format: ExportFormat }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-surface px-1.5 py-0.5 font-mono text-xs leading-[1.4] text-foreground">
      {exportFormatLabel(format)}
    </span>
  );
}

const STATUS_BADGES: Record<ExportStatus, { variant: BadgeProps["variant"]; className?: string }> =
  {
    queued: { variant: "outline", className: "text-muted-foreground" },
    running: { variant: "accent" },
    done: { variant: "success" },
    failed: { variant: "danger" },
    expired: { variant: "neutral" },
  };

/** The status pill; the word is the signal, the colour only reinforces it. */
export function StatusBadge({ status }: { status: ExportStatus }) {
  const { variant, className } = STATUS_BADGES[status];
  return (
    <Badge variant={variant} className={className}>
      {EXPORT_STATUS_LABELS[status]}
    </Badge>
  );
}

interface ExportFailureLineProps {
  errorCode: string | null;
  /** Stack the code above its message (the narrow tile) instead of beside it. */
  stacked?: boolean;
}

/** The failure code in mono and what to do about it, under a failed export. */
export function ExportFailureLine({ errorCode, stacked = false }: ExportFailureLineProps) {
  const { code, message } = exportFailure(errorCode);
  return (
    <div
      className={cn(
        "flex gap-x-2 gap-y-1 text-danger-text",
        stacked ? "flex-col" : "items-center pb-0.5 pl-[68px]",
      )}
    >
      <span className="flex shrink-0 items-center gap-1.5">
        <CircleAlert aria-hidden className="size-3.5" />
        <span className="font-mono text-label">{code}</span>
      </span>
      <span className="text-xs leading-[1.4] font-medium">{message}</span>
    </div>
  );
}

interface DownloadActionProps {
  exportId: string;
  /** Names the export for assistive technology: `describeExport(...).downloadLabel`. */
  label: string;
  className?: string;
}

/**
 * Figma "Settings/Icon button" with a download icon. Each click asks for a fresh link (they last
 * an hour), then the browser follows it; the spinner shows while that request is out.
 */
export function DownloadAction({ exportId, label, className }: DownloadActionProps) {
  const download = useDownloadExport();
  return (
    <Button
      variant="secondary"
      loading={download.isPending}
      aria-label={`Download ${label}`}
      className={cn("pr-4 pl-3.5 [&_svg]:size-4", className)}
      onClick={() => {
        download.mutate(exportId);
      }}
    >
      {download.isPending ? null : <Download aria-hidden />}
      Download
    </Button>
  );
}
