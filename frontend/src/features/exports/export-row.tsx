import { ValueOrUnknown } from "@/components/unknown-value";
import type { TraceExport } from "@/lib/api";
import { cn } from "@/lib/utils";

import { describeExport } from "./export-display";
import { DownloadAction, ExportFailureLine, FormatTag, StatusBadge } from "./export-parts";
import { isDownloadable, unknownExpiryReason, unknownReason } from "./export-status";

/*
 * The wide layout, from a 50rem list. Column widths are the Figma "Settings/Export row": format
 * 56, status 84, rows 60, size 60, created 104, expires 76 and the action 120, with the range and
 * filters taking the rest. Below that width the same export is an `ExportTile`.
 */

/** Overline labels above the rows; hidden from assistive technology, since each value carries its own screen-reader label. */
export function ExportColumnLabels() {
  return (
    <div
      aria-hidden
      className="hidden items-center gap-3 pt-2 pr-3 pb-0.5 pl-4 text-overline text-subtle-foreground uppercase @[50rem]:flex"
    >
      <span className="w-14 shrink-0">Format</span>
      <span className="min-w-0 flex-1">Range · filters</span>
      <span className="w-21 shrink-0">Status</span>
      <span className="w-15 shrink-0 text-right">Rows</span>
      <span className="w-15 shrink-0 text-right">Size</span>
      <span className="w-26 shrink-0">Created</span>
      <span className="w-19 shrink-0">Expires</span>
      <span className="w-30 shrink-0" />
    </div>
  );
}

interface ExportRowProps {
  entry: TraceExport;
}

export function ExportRow({ entry }: ExportRowProps) {
  const text = describeExport(entry);
  const expired = entry.status === "expired";
  // A finished export's figures are the point of the row; an expired one's are history.
  const figure = entry.status === "done" ? "text-foreground" : "text-subtle-foreground";

  return (
    <div
      className={cn(
        "hidden flex-col gap-1.5 rounded-tile py-2.5 pr-3 pl-4 @[50rem]:flex",
        expired ? "border border-dashed border-border" : "bg-surface-muted",
      )}
    >
      <div className="flex min-h-[42px] items-center gap-3">
        <div className="w-14 shrink-0">
          <FormatTag format={entry.format} />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-px">
          <span
            title={text.rangeDetail ?? undefined}
            className={cn(
              "truncate text-sm font-semibold",
              expired ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {text.range}
          </span>
          <span title={text.summary} className="truncate text-xs font-medium text-muted-foreground">
            {text.summary}
          </span>
        </div>
        <div className="w-21 shrink-0">
          <StatusBadge status={entry.status} />
        </div>
        <div className={cn("w-15 shrink-0 text-right text-sm font-medium tabular", figure)}>
          <span className="sr-only">Rows </span>
          <ValueOrUnknown value={text.rows} reason={unknownReason(entry.status)} />
        </div>
        <div className={cn("w-15 shrink-0 text-right text-sm font-medium tabular", figure)}>
          <span className="sr-only">Size </span>
          <ValueOrUnknown value={text.size} reason={unknownReason(entry.status)} />
        </div>
        <span className="w-26 shrink-0 text-sm whitespace-nowrap text-muted-foreground tabular">
          <span className="sr-only">Created </span>
          {text.created}
        </span>
        <div className="flex w-19 shrink-0 flex-col gap-px whitespace-nowrap">
          <div className="text-sm text-muted-foreground">
            <span className="sr-only">Expires </span>
            <ValueOrUnknown value={text.expires} reason={unknownExpiryReason(entry.status)} />
          </div>
          {text.expires !== null && text.expiresRelative !== null ? (
            <span className="text-xs font-medium text-subtle-foreground">
              {text.expiresRelative}
            </span>
          ) : null}
        </div>
        <div className="flex h-10 w-30 shrink-0 items-center justify-end">
          {isDownloadable(entry) ? (
            <DownloadAction exportId={entry.id} label={text.downloadLabel} />
          ) : null}
        </div>
      </div>
      {entry.status === "failed" ? <ExportFailureLine errorCode={entry.error_code} /> : null}
    </div>
  );
}
