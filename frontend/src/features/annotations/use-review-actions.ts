import { useRouter } from "@tanstack/react-router";
import { useState } from "react";

import { replaySpanOf } from "@/features/review";
import { useProjectParams, usePermission } from "@/features/shell";
import { useTraceQuery } from "@/features/traces";
import type { QueueItem } from "@/lib/api";

export const VIEWER_REASON = "Replay needs the member role.";
const NO_LLM_REASON = "Nothing to replay: this trace has no LLM span.";

/**
 * Work mode's "Replay failing span" and "Save as dataset item": the item's trace (the same cache
 * entry the trace card reads), where the replay opens, and the save dialog's open state.
 */
export function useReviewActions(item: QueueItem | null) {
  const { orgId, projectId } = useProjectParams();
  const router = useRouter();
  const canRun = usePermission("experiments:run");
  const [saveOpen, setSaveOpen] = useState(false);
  const query = useTraceQuery(item?.trace_id ?? "", { enabled: item !== null });
  const trace = item !== null && query.data?.trace_id === item.trace_id ? query.data : undefined;
  const spanId = item !== null && trace !== undefined ? replaySpanOf(item, trace) : null;
  const replay = item !== null && spanId !== null ? `${item.trace_id}:${spanId}` : null;
  const replayReason = !canRun
    ? VIEWER_REASON
    : trace !== undefined && spanId === null
      ? NO_LLM_REASON
      : null;
  const replayTo =
    replay === null || !canRun
      ? null
      : ({
          to: "/$orgId/$projectId/playground",
          params: { orgId, projectId },
          search: { replay },
        } as const);

  return {
    trace,
    replayTo,
    replayReason,
    saveOpen,
    setSaveOpen,
    /** Opens the replay in a new tab, so the claimed item stays claimed here. */
    openReplay: () => {
      if (replayTo !== null) {
        window.open(router.buildLocation(replayTo).href, "_blank", "noopener");
      }
    },
  };
}

export type ReviewActionsState = ReturnType<typeof useReviewActions>;
