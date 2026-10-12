import { CalendarDays, CircleAlert, Filter, Hash, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import {
  MAX_EXPORT_DAYS,
  exportFilterEntries,
  formatExportInstant,
  formatTraceCount,
  type ExportWindowProblem,
} from "@/features/exports";
import { cn } from "@/lib/utils";

import type { ExportDraft } from "./use-export-draft";

interface ExportSummaryProps {
  draft: ExportDraft;
  /** Set when the window cannot be exported: the box and the time range turn red. */
  problem: ExportWindowProblem | null;
}

/**
 * "What's exported" (Figma "Summary box"): the trace list's time range, its filters and how many
 * traces match, read-only. The times are the viewer's local time, as in the trace list.
 */
export function ExportSummary({ draft, problem }: ExportSummaryProps) {
  const { filters, rangeLabel, rows, notApplied } = draft;
  const entries = exportFilterEntries(filters);
  const start = formatExportInstant(filters.from);
  const end = formatExportInstant(filters.to);

  return (
    <div className="flex flex-col gap-2">
      <p className="text-label leading-5 font-semibold text-foreground">What&apos;s exported</p>
      <div
        className={cn(
          "flex flex-col gap-3 rounded-input bg-surface-muted px-4 py-3.5",
          problem ? "border-[1.5px] border-danger" : "border border-border",
        )}
      >
        <SummaryRow icon={CalendarDays} label="Time range" align="start">
          <div className={cn("flex flex-col gap-0.5", problem && "text-danger-text")}>
            <p className={cn("text-sm font-semibold", !problem && "text-foreground")}>
              {start ?? "Unknown"} – {end ?? "Unknown"}
            </p>
            <p className={cn("text-xs font-medium", !problem && "text-muted-foreground")}>
              {problem?.kind === "too-long"
                ? `${problem.days} days · the limit is ${MAX_EXPORT_DAYS}`
                : rangeLabel}
            </p>
          </div>
        </SummaryRow>
        <SummaryRow icon={Filter} label="Filters">
          {entries.length > 0 ? (
            <ul aria-label="Active filters" className="flex flex-wrap gap-1.5">
              {entries.map((entry) => (
                <li key={entry.key} className="min-w-0">
                  <Badge
                    variant="accent"
                    title={`${entry.key}: ${entry.value}`}
                    className="max-w-full"
                  >
                    <span className="truncate">
                      {entry.key}: {entry.value}
                    </span>
                  </Badge>
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-sm font-semibold text-muted-foreground">All traces</span>
          )}
        </SummaryRow>
        <SummaryRow icon={Hash} label="Rows">
          {rows === null ? (
            <UnknownValue
              reason="Known when the export finishes."
              className="text-sm font-semibold"
            />
          ) : (
            <span className="text-sm font-semibold text-foreground tabular">
              {formatTraceCount(rows)}
            </span>
          )}
        </SummaryRow>
      </div>
      {problem ? (
        <p
          role="alert"
          className="flex items-start gap-1.5 text-xs leading-[1.4] font-medium text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {problem.kind === "too-long"
            ? `Exports cover at most ${MAX_EXPORT_DAYS} days. Narrow the time range on the Traces page, then export again.`
            : "The time range is not valid. Pick another one on the Traces page, then export again."}
        </p>
      ) : (
        <p className="text-xs leading-[1.4] font-medium text-muted-foreground">
          Uses the filters and time range from the trace list. Change them on the Traces page.
        </p>
      )}
      {notApplied.length > 0 ? (
        <p className="flex items-start gap-1.5 text-xs leading-[1.4] font-medium text-warning">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {`Exports don’t apply ${notApplied.join(", ")} yet, so the file also includes the traces ${notApplied.length === 1 ? "that filter hides" : "those filters hide"}.`}
        </p>
      ) : null}
    </div>
  );
}

interface SummaryRowProps {
  icon: LucideIcon;
  label: string;
  align?: "start" | "center";
  children: ReactNode;
}

function SummaryRow({ icon: Icon, label, align = "center", children }: SummaryRowProps) {
  return (
    <div className={cn("flex gap-3", align === "start" ? "items-start" : "items-center")}>
      <div className="flex h-[21px] w-24 shrink-0 items-center gap-2 text-sm text-muted-foreground sm:w-[120px]">
        <Icon aria-hidden className="size-4 shrink-0 text-foreground" />
        {label}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
