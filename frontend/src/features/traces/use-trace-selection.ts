import { useState } from "react";

import type { TraceSummary } from "@/lib/api";

/** The traces ticked in the list, for "Add to dataset" and "Add to queue" in bulk. */
export interface TraceSelection {
  selected: TraceSummary[];
  isSelected: (traceId: string) => boolean;
  toggle: (trace: TraceSummary) => void;
  /** "all" when every shown trace is ticked, "some" when part of them, "none" otherwise. */
  state: "all" | "some" | "none";
  toggleAll: () => void;
  clear: () => void;
}

/**
 * Selection over the traces on screen. It keeps the summaries (name and id) so the dialogs can
 * list them, and forgets traces that leave the list when the filters change.
 */
export function useTraceSelection(shown: TraceSummary[]): TraceSelection {
  const [picked, setPicked] = useState<Map<string, TraceSummary>>(new Map());
  const shownIds = new Set(shown.map((trace) => trace.trace_id));
  const selected = [...picked.values()].filter((trace) => shownIds.has(trace.trace_id));
  // A pick the list no longer shows (filters, project) is dropped, so it can't come back ticked
  // when the filter is undone. Adjusted during render, React's pattern for derived resets.
  if (selected.length !== picked.size) {
    setPicked(new Map(selected.map((trace) => [trace.trace_id, trace])));
  }
  const state = selected.length === 0 ? "none" : selected.length === shown.length ? "all" : "some";

  return {
    selected,
    state,
    isSelected: (traceId) => shownIds.has(traceId) && picked.has(traceId),
    toggle: (trace) => {
      setPicked((current) => {
        const next = new Map(current);
        if (next.has(trace.trace_id)) next.delete(trace.trace_id);
        else next.set(trace.trace_id, trace);
        return next;
      });
    },
    toggleAll: () => {
      setPicked(
        state === "all" ? new Map() : new Map(shown.map((trace) => [trace.trace_id, trace])),
      );
    },
    clear: () => {
      setPicked(new Map());
    },
  };
}
