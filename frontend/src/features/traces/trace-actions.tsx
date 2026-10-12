import { getRouteApi } from "@tanstack/react-router";
import { ClipboardCheck, Database } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AddToDatasetDialog, AddToQueueDialog } from "@/features/datasets";
import { usePermission } from "@/features/shell";
import type { TraceDetail } from "@/lib/api";

import { buildSpanTree } from "./span-tree";

const traceRoute = getRouteApi("/_authed/$orgId/$projectId/traces/$traceId");

/**
 * Figma trace header actions: "Add to dataset" and "Add to queue". Both offer the span selected
 * in the waterfall (the same default the page shows: the first root span).
 */
export function TraceActions({ trace }: { trace: TraceDetail }) {
  const search = traceRoute.useSearch();
  const canAddToDataset = usePermission("datasets:write");
  const canAddToQueue = usePermission("annotations:write");
  if (!canAddToDataset && !canAddToQueue) {
    return null;
  }
  const selectedSpan =
    trace.spans.find((span) => span.span_id === search.span) ??
    buildSpanTree(trace.spans)[0]?.span ??
    null;
  const ref = { trace_id: trace.trace_id, name: trace.name };

  return (
    <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
      {canAddToDataset ? (
        <AddToDatasetDialog
          target={{ kind: "trace", trace, selectedSpan }}
          trigger={
            <Button size="sm">
              <Database aria-hidden />
              Add to dataset
            </Button>
          }
        />
      ) : null}
      {canAddToQueue ? (
        <AddToQueueDialog
          traces={[ref]}
          selectedSpan={selectedSpan}
          trigger={
            <Button size="sm">
              <ClipboardCheck aria-hidden />
              Add to queue
            </Button>
          }
        />
      ) : null}
    </div>
  );
}
