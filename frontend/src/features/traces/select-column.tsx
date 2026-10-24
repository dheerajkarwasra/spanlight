import type { DataColumn } from "@/components/data-table";
import type { TraceSummary } from "@/lib/api";

import { RowCheckbox } from "./row-checkbox";
import type { TraceSelection } from "./use-trace-selection";

/** The traces table's sticky select column: a box per row and select-all in the header. */
export function selectColumn(selection: TraceSelection | undefined): DataColumn<TraceSummary> {
  return {
    id: "select",
    header: "Select",
    // The 24 px hit area overflows the 40 px column's padding by design.
    headerCell: selection ? (
      <span className="-mx-1 flex">
        <RowCheckbox
          checked={selection.state === "some" ? "indeterminate" : selection.state === "all"}
          onCheckedChange={selection.toggleAll}
          label="Select every trace shown"
        />
      </span>
    ) : (
      <span className="sr-only">Select</span>
    ),
    width: "40px",
    sticky: true,
    cell: (trace) =>
      selection ? (
        <span className="-mx-1 flex">
          <RowCheckbox
            checked={selection.isSelected(trace.trace_id)}
            onCheckedChange={() => {
              selection.toggle(trace);
            }}
            label={`Select ${trace.name ?? "unnamed trace"} ${trace.trace_id}`}
          />
        </span>
      ) : null,
  };
}
