import { Card } from "@/components/ui/card";
import { progressRatio, progressSummary, progressTotal } from "@/features/annotations/queue-format";
import type { QueueProgress } from "@/lib/api";

import { ReasonChip } from "./reason-chip";
import { REVIEW_REASONS } from "./reason-copy";

/**
 * Figma "Queue detail / By reason": the queue's progress per reason. The bar is decorative; the
 * words beside it carry the numbers.
 */
export function ReasonProgress({ progress }: { progress: QueueProgress }) {
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-base font-bold text-foreground">By reason</h2>
        <span className="text-xs font-medium text-muted-foreground">done of queued</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {REVIEW_REASONS.map((reason) => {
          const counts = progress.by_reason[reason];
          return (
            <li
              key={reason}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[132px_minmax(0,1fr)_150px]"
            >
              <span className="col-span-2 sm:col-span-1">
                <ReasonChip reason={reason} />
              </span>
              <span aria-hidden className="h-1.5 overflow-hidden rounded-full bg-surface-muted">
                <span
                  className="block h-full rounded-full bg-accent"
                  style={{ width: `${String(Math.round(progressRatio(counts) * 1000) / 10)}%` }}
                />
              </span>
              <span className="text-xs font-medium text-muted-foreground">
                {progressTotal(counts) === 0 ? "None queued" : progressSummary(counts)}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
