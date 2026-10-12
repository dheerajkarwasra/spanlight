import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Skeleton } from "@/components/ui/skeleton";

import { BudgetMeter } from "./budget-meter";
import { useEvaluatorBudgetQuery } from "./evaluator-queries";

/** Figma "Budget": a judge's month so far. Only judges have one, so only they show it. */
export function BudgetCard({ evaluatorId }: { evaluatorId: string }) {
  const query = useEvaluatorBudgetQuery(evaluatorId);
  return (
    <SectionCard
      title="Budget"
      description="Resets on the first of each month, UTC."
      className="gap-4"
    >
      {query.isPending ? (
        <Skeleton
          role="status"
          aria-label="Loading the budget"
          className="h-[120px] rounded-tile"
        />
      ) : query.data === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the budget"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <BudgetMeter status={query.data} />
      )}
    </SectionCard>
  );
}
