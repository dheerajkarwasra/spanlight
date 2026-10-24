import type { Budget } from "@/lib/api";
import { cn } from "@/lib/utils";

import { BUDGET_WARNING_RATIO, budgetRatio } from "./needs-attention";

/** Budget use for the current period, with ticks at 50, 80 and 100 %. */
export function BudgetBar({ budget }: { budget: Budget }) {
  const ratio = budgetRatio(budget);
  if (ratio === null) {
    return null;
  }
  let fill = "bg-accent";
  if (ratio >= 1) {
    fill = "bg-danger";
  } else if (ratio >= BUDGET_WARNING_RATIO) {
    fill = "bg-warning";
  }
  return (
    <div
      className="mt-auto flex flex-col gap-1 pt-2"
      title={`${budget.name}: ${String(Math.floor(ratio * 100))}% used`}
    >
      <div aria-hidden className="relative h-1.5 rounded-full bg-surface-muted">
        <div
          className={cn("h-full rounded-full", fill)}
          style={{ width: `${String(Math.min(1, ratio) * 100)}%` }}
        />
        {[50, 80].map((tick) => (
          <span
            key={tick}
            className="absolute -top-0.5 h-2.5 w-px bg-border-strong"
            style={{ left: `${String(tick)}%` }}
          />
        ))}
      </div>
      <p className="text-2xs text-muted-foreground tabular">
        {Math.floor(ratio * 100)}% of {budget.period} budget
      </p>
    </div>
  );
}
