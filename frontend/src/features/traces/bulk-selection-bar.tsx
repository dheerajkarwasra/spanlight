import { Link } from "@tanstack/react-router";
import { GitCompareArrows, X } from "lucide-react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { AddToDatasetDialog, AddToQueueDialog } from "@/features/datasets";
import { usePermission } from "@/features/shell";
import { pluralize } from "@/lib/format";

import { ExportDialog } from "./export-dialog";
import { MAX_SELECTED_EXPORT } from "./use-export-draft";
import type { TraceSelection } from "./use-trace-selection";

/**
 * Figma "Traces — Bulk selection": a floating ink bar while rows are ticked, with "Add to
 * dataset", "Add to queue", "Export selection" (at most 200 traces), "Compare" (exactly two traces; the earlier one is the baseline) and
 * "Clear". The table (and so the selection) exists from `md` up.
 */
export function BulkSelectionBar({ selection }: { selection: TraceSelection }) {
  const canAddToDataset = usePermission("datasets:write");
  const canAddToQueue = usePermission("annotations:write");
  const canExport = usePermission("export:create");
  const count = selection.selected.length;
  if (count === 0) {
    return null;
  }
  // The bar leaves with the selection, so focus goes to the table instead of the page.
  const finish = () => {
    selection.clear();
    const table = document.querySelector<HTMLElement>('[role="table"][aria-label="Traces"]');
    if (table) {
      table.tabIndex = -1;
      table.focus();
    }
  };
  const [a, b] = [...selection.selected].sort((x, y) => x.started_at.localeCompare(y.started_at));
  const traces = selection.selected.map((trace) => ({
    trace_id: trace.trace_id,
    name: trace.name,
  }));

  return (
    <div
      role="region"
      aria-label="Selected traces"
      className="fixed bottom-6 left-1/2 z-40 hidden -translate-x-1/2 items-center gap-3 rounded-full bg-hero-card py-2 pr-3 pl-5 text-hero-card-foreground shadow-lg md:flex dark:border dark:border-border"
    >
      <p className="text-sm font-semibold whitespace-nowrap" aria-live="polite">
        {pluralize(count, "trace")} selected
      </p>
      <span aria-hidden className="h-6 w-px bg-rail-tile" />
      {canAddToDataset ? (
        <AddToDatasetDialog
          target={{ kind: "traces", traces }}
          onAdded={finish}
          trigger={<Button variant="highlight">Add to dataset</Button>}
        />
      ) : null}
      {canAddToQueue ? (
        <AddToQueueDialog
          traces={traces}
          onAdded={finish}
          trigger={<Button>Add to queue</Button>}
        />
      ) : null}
      {canExport && count > MAX_SELECTED_EXPORT ? (
        <DisabledReason
          reason={`Exports take at most ${String(MAX_SELECTED_EXPORT)} selected traces.`}
        >
          <Button disabled>Export selection</Button>
        </DisabledReason>
      ) : canExport ? (
        <ExportDialog selection={selection.selected} trigger={<Button>Export selection</Button>} />
      ) : null}
      {count === 2 && a && b ? (
        <Button asChild>
          <Link
            to="/$orgId/$projectId/traces/compare"
            from="/$orgId/$projectId/traces"
            search={(prev) => ({
              range: prev.range,
              from: prev.from,
              to: prev.to,
              env: prev.env,
              a: a.trace_id,
              b: b.trace_id,
            })}
          >
            <GitCompareArrows aria-hidden />
            Compare
          </Link>
        </Button>
      ) : null}
      <Button
        variant="ghost"
        className="text-hero-card-foreground hover:bg-rail-tile"
        onClick={selection.clear}
      >
        <X aria-hidden />
        Clear
      </Button>
    </div>
  );
}
