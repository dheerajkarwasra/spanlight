import { Wallet } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { cn } from "@/lib/utils";

import { useReplayBudgetQuery } from "./replay-queries";
import { budgetRatio, budgetText, isBudgetSpent } from "./replay-state";

const UNREAD_REASON = "Couldn't read this month's replay budget. Reload to try again.";

/**
 * Figma "Replay budget meter": "$1.20 of $5.00 this month" over a thin bar. The bar repeats the
 * printed figures, so it is hidden from assistive technology; a spent budget also says so in text.
 */
export function ReplayBudgetMeter() {
  const query = useReplayBudgetQuery({ enabled: true });
  if (query.isPending) {
    return (
      <div role="status" aria-label="Loading the replay budget">
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }
  const budget = query.data;
  const ratio = budget ? budgetRatio(budget) : null;
  const spent = isBudgetSpent(budget);

  return (
    <div className="flex flex-col gap-1.5">
      <p className="flex items-center gap-1.5 text-xs leading-5">
        <Wallet aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium text-foreground tabular">
          {budget ? budgetText(budget) : <UnknownValue reason={UNREAD_REASON} />}
        </span>
        {spent ? <span className="sr-only">Spent for this month.</span> : null}
        <span className="ml-auto text-muted-foreground">Replay budget</span>
      </p>
      <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-border">
        {ratio === null ? null : (
          <div
            className={cn("h-full rounded-full", spent ? "bg-muted-foreground" : "bg-accent")}
            style={{ width: `${String(ratio * 100)}%` }}
          />
        )}
      </div>
    </div>
  );
}
