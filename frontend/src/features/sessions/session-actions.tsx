import { ClipboardCheck, Database } from "lucide-react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { AddToDatasetDialog, AddToQueueDialog } from "@/features/datasets";
import { usePermission } from "@/features/shell";
import type { TraceSummary } from "@/lib/api";
import { pluralize } from "@/lib/format";

/** The bulk add routes take at most this many traces at once. */
const MAX_TRACES = 200;

/**
 * Spec §8 S6: "Add to queue" and "Add to dataset" over the session's loaded turns, oldest first;
 * past 200 turns the buttons say only the first 200 go, and while earlier turns aren't loaded
 * (`partial`) they say only the loaded turns go. Disabled, with the reason, without the permission.
 */
export function SessionActions({
  traces,
  partial = false,
}: {
  traces: readonly TraceSummary[];
  partial?: boolean;
}) {
  const canAddToDataset = usePermission("datasets:write");
  const canAddToQueue = usePermission("annotations:write");
  const refs = traces
    .slice(0, MAX_TRACES)
    .map((trace) => ({ trace_id: trace.trace_id, name: trace.name }));
  const suffix =
    traces.length > MAX_TRACES
      ? ` · First ${MAX_TRACES} turns`
      : partial
        ? ` · ${pluralize(traces.length, "loaded turn")}`
        : "";
  const queue = (
    <Button disabled={!canAddToQueue}>
      <ClipboardCheck aria-hidden />
      Add to queue{suffix}
    </Button>
  );
  const dataset = (
    <Button variant="primary" disabled={!canAddToDataset}>
      <Database aria-hidden />
      Add to dataset{suffix}
    </Button>
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      {canAddToQueue ? (
        <AddToQueueDialog traces={refs} trigger={queue} />
      ) : (
        <DisabledReason reason="Viewers can't add traces to review queues.">{queue}</DisabledReason>
      )}
      {canAddToDataset ? (
        <AddToDatasetDialog target={{ kind: "traces", traces: refs }} trigger={dataset} />
      ) : (
        <DisabledReason reason="Viewers can't add traces to datasets.">{dataset}</DisabledReason>
      )}
    </div>
  );
}
