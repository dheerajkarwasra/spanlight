import { CalendarDays, ListFilter, PieChart, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { SummaryRow } from "@/features/exports";
import { RANGE_LABELS } from "@/lib/time-range";

import {
  exportWindowText,
  groupLabel,
  sourceOf,
  type AttributionExportView,
} from "./attribution-export";

/** Figma "Attribution / Export dialog" summary box: window, group, filters and source, read-only. */
export function AttributionExportSummary({ view }: { view: AttributionExportView }) {
  const rollup = sourceOf(view) === "rollup";
  const rangeLabel = RANGE_LABELS[view.range.value];

  return (
    <div className="flex flex-col gap-2">
      <p className="text-label leading-5 font-semibold text-foreground">What&apos;s exported</p>
      <div className="flex flex-col gap-3 rounded-input border border-border bg-surface-muted px-4 py-3.5">
        <SummaryRow icon={CalendarDays} label="Window" align="start" stackOnPhone>
          <div className="flex flex-col gap-0.5">
            <p className="text-sm font-semibold text-foreground">{exportWindowText(view)}</p>
            <p className="text-xs font-medium text-muted-foreground">
              {rollup ? `${rangeLabel}, as whole UTC days` : rangeLabel}
            </p>
          </div>
        </SummaryRow>
        <SummaryRow icon={PieChart} label="Group" stackOnPhone>
          <p className="flex min-w-0 items-baseline gap-1.5 text-sm font-semibold text-foreground">
            {groupLabel(view.groupBy)}
            {view.groupBy === "metadata" && view.key ? (
              <>
                <span aria-hidden>·</span>
                <code className="truncate font-mono text-[13px] font-normal">{view.key}</code>
              </>
            ) : null}
          </p>
        </SummaryRow>
        <SummaryRow icon={ListFilter} label="Filters" stackOnPhone>
          {view.environment ? (
            <Badge
              variant="accent"
              title={`environment: ${view.environment}`}
              className="max-w-full"
            >
              <span className="truncate">environment: {view.environment}</span>
            </Badge>
          ) : (
            <span className="text-sm font-semibold text-muted-foreground">All environments</span>
          )}
        </SummaryRow>
        <SummaryRow icon={RefreshCw} label="Source" stackOnPhone>
          <p className="text-sm font-semibold text-foreground">
            {rollup ? "Daily rollup, updated every 15 min" : "Live, read from stored spans"}
          </p>
        </SummaryRow>
      </div>
      <p className="text-xs leading-[1.4] font-medium text-muted-foreground">
        Uses the group, key, window and environment from the attribution view. A value that starts
        with =, +, -, @ or a tab opens as text in a spreadsheet, never as a formula.
      </p>
    </div>
  );
}
