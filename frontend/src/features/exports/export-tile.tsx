import type { ReactNode } from "react";

import { ValueOrUnknown } from "@/components/unknown-value";
import type { TraceExport } from "@/lib/api";
import { cn } from "@/lib/utils";

import { describeExport } from "./export-display";
import { DownloadAction, ExportFailureLine, FormatTag, StatusBadge } from "./export-parts";
import { isDownloadable, unknownExpiryReason, unknownReason } from "./export-status";

interface ExportTileProps {
  entry: TraceExport;
}

/**
 * One export as a stacked tile, below a 50rem list (Figma "Settings/Export tile (mobile)"): format,
 * range and status on top, the filters, a two-by-two grid of rows, size, created and expires, then
 * the download button or the failure. Above that width the same export is an `ExportRow`.
 */
export function ExportTile({ entry }: ExportTileProps) {
  const text = describeExport(entry);
  const expired = entry.status === "expired";
  const figure = entry.status === "done" ? "text-foreground" : "text-subtle-foreground";

  return (
    <div
      className={cn(
        "flex flex-col gap-2.5 rounded-tile px-4 py-3.5 @[50rem]:hidden",
        expired ? "border border-dashed border-border" : "bg-surface-muted",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <FormatTag format={entry.format} />
          <span
            title={text.rangeDetail ?? undefined}
            className={cn(
              "min-w-0 text-sm font-semibold [overflow-wrap:anywhere]",
              expired ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {text.range}
          </span>
        </span>
        <StatusBadge status={entry.status} />
      </div>
      <p className="text-xs font-medium [overflow-wrap:anywhere] text-muted-foreground">
        {text.summary}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5">
        <TilePair label="Rows">
          <span className={cn("text-sm font-medium tabular", figure)}>
            <ValueOrUnknown value={text.rows} reason={unknownReason(entry.status)} />
          </span>
        </TilePair>
        <TilePair label="Size">
          <span className={cn("text-sm font-medium tabular", figure)}>
            <ValueOrUnknown value={text.size} reason={unknownReason(entry.status)} />
          </span>
        </TilePair>
        <TilePair label="Created">
          <span className="text-sm text-muted-foreground tabular">{text.created}</span>
        </TilePair>
        <TilePair label="Expires">
          <span className="flex items-baseline gap-1.5 text-sm whitespace-nowrap text-muted-foreground">
            <ValueOrUnknown value={text.expires} reason={unknownExpiryReason(entry.status)} />
            {text.expires !== null && text.expiresRelative !== null ? (
              <span className="text-xs font-medium text-subtle-foreground">
                {text.expiresRelative}
              </span>
            ) : null}
          </span>
        </TilePair>
      </dl>
      {isDownloadable(entry) ? (
        <DownloadAction exportId={entry.id} label={text.downloadLabel} className="w-full" />
      ) : null}
      {entry.status === "failed" ? (
        <ExportFailureLine errorCode={entry.error_code} stacked />
      ) : null}
    </div>
  );
}

function TilePair({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-overline text-subtle-foreground uppercase">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
